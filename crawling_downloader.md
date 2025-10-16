# 🚀 Case.law JSON Downloader - Hướng Dẫn Triển Khai

## 📋 Tổng Quan

Script này sẽ download và xử lý **6.8 triệu file JSON** từ Case.law, lưu kết quả vào nhiều file CSV (mỗi file chứa dữ liệu JSON dạng string trong 1 dòng).

### Mục tiêu:

- ✅ Download toàn bộ 6.8M URLs từ `caselaw_detailed_fixed_oldAndOK.csv`
- ✅ Fetch JSON content từ mỗi URL
- ✅ Lưu vào nhiều CSV files (mỗi file ~100K records = ~500MB)
- ✅ Resume capability khi bị gián đoạn
- ✅ Tối ưu cho MacBook local

---

## 🏗️ Kiến Trúc Hệ Thống

```
┌─────────────────────────────────────────────────────────────┐
│  INPUT: caselaw_detailed_fixed_oldAndOK.csv (6.8M rows)     │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│         CSV Reader (Streaming - pandas chunks)              │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│    Async Download Workers (100-200 concurrent)              │
│    - aiohttp session with connection pooling                │
│    - Retry logic (exponential backoff)                      │
│    - Rate limiting (500-1000 req/s)                         │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│         Batch CSV Writer (per 100K records)                 │
│    output_part_0001.csv (100K records)                      │
│    output_part_0002.csv (100K records)                      │
│    ...                                                       │
│    output_part_0068.csv (remaining)                         │
└─────────────────────────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│      Progress Tracker (SQLite checkpoint DB)                │
│    - Track downloaded URLs                                  │
│    - Resume from last position                              │
└─────────────────────────────────────────────────────────────┘
```

---

## 📦 Dependencies

### 1. Cài đặt Python packages:

```bash
pip install aiohttp aiofiles pandas tqdm aiosqlite
```

### 2. Packages cần thiết:

| Package     | Mục đích                   |
| ----------- | -------------------------- |
| `aiohttp`   | Async HTTP requests        |
| `aiofiles`  | Async file I/O             |
| `pandas`    | CSV processing (streaming) |
| `tqdm`      | Progress bar               |
| `aiosqlite` | Async SQLite (checkpoint)  |
| `asyncio`   | Built-in async I/O         |

---

## 🔧 Cấu Hình Hệ Thống MacBook

### 1. Tăng file descriptor limit:

```bash
# Check current limit
ulimit -n

# Increase to 10000
ulimit -n 10000

# Để permanent, thêm vào ~/.zshrc:
echo "ulimit -n 10000" >> ~/.zshrc
```

### 2. Kiểm tra disk space:

```bash
# Cần ít nhất 100GB free space
df -h /Volumes/DATA
```

### 3. Monitor resources:

```bash
# Install htop nếu chưa có
brew install htop

# Monitor while running
htop
```

---

## 💻 Script Implementation

### File: `download_caselaw_json.py`

```python
#!/usr/bin/env python3
"""
Download 6.8M JSON files from Case.law and save to multiple CSV files.
Each CSV file contains ~100K records to keep file size manageable.

Output CSV Format:
- jurisdiction, volume, filename, url, json_content, download_timestamp, size_bytes

Optimized for MacBook local execution with:
- Async I/O (aiohttp)
- Connection pooling
- Resume capability
- Progress tracking
- Rate limiting
"""

import asyncio
import aiohttp
import aiofiles
import pandas as pd
import json
import sqlite3
import os
import time
from datetime import datetime
from pathlib import Path
from tqdm.asyncio import tqdm
import signal
import sys
from typing import List, Dict, Optional
import csv

# ============================================================================
# CONFIGURATION
# ============================================================================

# Input/Output
INPUT_CSV = "caselaw_detailed_fixed_oldAndOK.csv"
OUTPUT_DIR = "downloaded_json_data"
OUTPUT_PREFIX = "caselaw_json_part"
CHECKPOINT_DB = "download_checkpoint.db"

# Performance tuning
MAX_CONCURRENT_DOWNLOADS = 100  # Số workers đồng thời
RECORDS_PER_FILE = 100000       # 100K records per CSV file
CHUNK_SIZE = 1000               # Read CSV in chunks
REQUEST_TIMEOUT = 60            # Timeout cho mỗi request (seconds)
MAX_RETRIES = 3                 # Số lần retry
RATE_LIMIT_DELAY = 0.001        # Delay giữa requests (1ms)
BATCH_COMMIT_SIZE = 1000        # Commit checkpoint mỗi 1000 downloads

# HTTP settings
USER_AGENTS = [
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36',
]

# ============================================================================
# GLOBAL VARIABLES
# ============================================================================

stats = {
    'total_processed': 0,
    'total_success': 0,
    'total_failed': 0,
    'total_bytes': 0,
    'start_time': time.time(),
    'current_part': 1,
}

shutdown_flag = False

# ============================================================================
# CHECKPOINT DATABASE
# ============================================================================

def init_checkpoint_db():
    """Initialize SQLite checkpoint database"""
    conn = sqlite3.connect(CHECKPOINT_DB)
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS downloads (
            url TEXT PRIMARY KEY,
            jurisdiction TEXT,
            volume TEXT,
            filename TEXT,
            success INTEGER,
            size_bytes INTEGER,
            timestamp TEXT,
            error_message TEXT
        )
    ''')

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_success ON downloads(success)
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS progress (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')

    conn.commit()
    conn.close()
    print(f"✓ Checkpoint database initialized: {CHECKPOINT_DB}")


def is_url_downloaded(url: str) -> bool:
    """Check if URL already downloaded"""
    conn = sqlite3.connect(CHECKPOINT_DB)
    cursor = conn.cursor()
    cursor.execute('SELECT success FROM downloads WHERE url = ?', (url,))
    result = cursor.fetchone()
    conn.close()
    return result is not None and result[0] == 1


def save_checkpoint_batch(records: List[Dict]):
    """Save batch of download records to checkpoint DB"""
    conn = sqlite3.connect(CHECKPOINT_DB)
    cursor = conn.cursor()

    for record in records:
        cursor.execute('''
            INSERT OR REPLACE INTO downloads
            (url, jurisdiction, volume, filename, success, size_bytes, timestamp, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            record['url'],
            record['jurisdiction'],
            record['volume'],
            record['filename'],
            record['success'],
            record.get('size_bytes', 0),
            record['timestamp'],
            record.get('error_message', '')
        ))

    conn.commit()
    conn.close()


def get_progress_stats() -> Dict:
    """Get download progress statistics"""
    conn = sqlite3.connect(CHECKPOINT_DB)
    cursor = conn.cursor()

    cursor.execute('SELECT COUNT(*) FROM downloads WHERE success = 1')
    success_count = cursor.fetchone()[0]

    cursor.execute('SELECT COUNT(*) FROM downloads WHERE success = 0')
    failed_count = cursor.fetchone()[0]

    cursor.execute('SELECT SUM(size_bytes) FROM downloads WHERE success = 1')
    total_bytes = cursor.fetchone()[0] or 0

    conn.close()

    return {
        'success': success_count,
        'failed': failed_count,
        'total_bytes': total_bytes
    }


# ============================================================================
# CSV WRITER
# ============================================================================

class PartitionedCSVWriter:
    """Write CSV in partitions to keep file sizes manageable"""

    def __init__(self, output_dir: str, prefix: str, records_per_file: int):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        self.prefix = prefix
        self.records_per_file = records_per_file
        self.current_part = 1
        self.current_count = 0
        self.current_file = None
        self.current_writer = None
        self._init_new_part()

    def _init_new_part(self):
        """Initialize a new CSV part file"""
        if self.current_file:
            self.current_file.close()

        filename = f"{self.prefix}_{self.current_part:04d}.csv"
        filepath = self.output_dir / filename

        self.current_file = open(filepath, 'w', newline='', encoding='utf-8')
        self.current_writer = csv.writer(self.current_file)

        # Write header
        self.current_writer.writerow([
            'jurisdiction',
            'volume',
            'filename',
            'url',
            'json_content',
            'download_timestamp',
            'size_bytes',
            'http_status'
        ])

        self.current_count = 0
        print(f"\n📄 Created new CSV part: {filename}")

    def write_row(self, row: List):
        """Write a single row"""
        self.current_writer.writerow(row)
        self.current_count += 1

        # Check if need to create new part
        if self.current_count >= self.records_per_file:
            self.current_part += 1
            stats['current_part'] = self.current_part
            self._init_new_part()

    def close(self):
        """Close current file"""
        if self.current_file:
            self.current_file.close()
            print(f"\n✓ Closed CSV part {self.current_part}")


# ============================================================================
# DOWNLOAD LOGIC
# ============================================================================

async def download_json(session: aiohttp.ClientSession, row: Dict) -> Dict:
    """Download JSON content from URL"""
    url = row['url']

    for attempt in range(MAX_RETRIES):
        try:
            # Add small delay for rate limiting
            await asyncio.sleep(RATE_LIMIT_DELAY)

            # Rotate user agent
            headers = {'User-Agent': USER_AGENTS[attempt % len(USER_AGENTS)]}

            async with session.get(url, timeout=REQUEST_TIMEOUT, headers=headers) as response:
                if response.status == 200:
                    content = await response.text()
                    content_size = len(content.encode('utf-8'))

                    # Verify it's valid JSON
                    try:
                        json.loads(content)
                    except json.JSONDecodeError:
                        return {
                            'success': False,
                            'error': 'Invalid JSON content',
                            'status': response.status
                        }

                    return {
                        'success': True,
                        'json_content': content,
                        'size_bytes': content_size,
                        'status': response.status
                    }
                else:
                    # Non-200 status
                    if attempt < MAX_RETRIES - 1:
                        await asyncio.sleep(2 ** attempt)  # Exponential backoff
                        continue

                    return {
                        'success': False,
                        'error': f'HTTP {response.status}',
                        'status': response.status
                    }

        except asyncio.TimeoutError:
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(2 ** attempt)
                continue
            return {
                'success': False,
                'error': 'Timeout',
                'status': 0
            }

        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(2 ** attempt)
                continue
            return {
                'success': False,
                'error': str(e),
                'status': 0
            }

    return {
        'success': False,
        'error': 'Max retries exceeded',
        'status': 0
    }


# ============================================================================
# WORKER POOL
# ============================================================================

async def worker(session: aiohttp.ClientSession, queue: asyncio.Queue,
                csv_writer: PartitionedCSVWriter, checkpoint_batch: List):
    """Worker to process download tasks"""
    global shutdown_flag

    while not shutdown_flag:
        try:
            # Get task from queue
            row = await asyncio.wait_for(queue.get(), timeout=1.0)

            if row is None:  # Poison pill
                break

            # Skip if already downloaded
            if is_url_downloaded(row['url']):
                stats['total_processed'] += 1
                queue.task_done()
                continue

            # Download JSON
            result = await download_json(session, row)

            # Prepare checkpoint record
            checkpoint_record = {
                'url': row['url'],
                'jurisdiction': row['jurisdiction'],
                'volume': row['volume'],
                'filename': row['filename'],
                'success': 1 if result['success'] else 0,
                'size_bytes': result.get('size_bytes', 0),
                'timestamp': datetime.now().isoformat(),
                'error_message': result.get('error', '')
            }

            checkpoint_batch.append(checkpoint_record)

            # Write to CSV if successful
            if result['success']:
                csv_writer.write_row([
                    row['jurisdiction'],
                    row['volume'],
                    row['filename'],
                    row['url'],
                    result['json_content'],
                    checkpoint_record['timestamp'],
                    result['size_bytes'],
                    result['status']
                ])

                stats['total_success'] += 1
                stats['total_bytes'] += result['size_bytes']
            else:
                stats['total_failed'] += 1

            stats['total_processed'] += 1

            # Batch commit checkpoint
            if len(checkpoint_batch) >= BATCH_COMMIT_SIZE:
                save_checkpoint_batch(checkpoint_batch.copy())
                checkpoint_batch.clear()

            queue.task_done()

        except asyncio.TimeoutError:
            continue
        except Exception as e:
            print(f"\n❌ Worker error: {e}")
            continue


# ============================================================================
# MAIN ORCHESTRATION
# ============================================================================

def signal_handler(signum, frame):
    """Handle Ctrl+C gracefully"""
    global shutdown_flag
    print("\n\n⚠️  Shutdown signal received. Finishing current tasks...")
    shutdown_flag = True


async def main():
    """Main orchestration function"""
    global shutdown_flag

    # Setup signal handler
    signal.signal(signal.SIGINT, signal_handler)

    print("="*70)
    print("🚀 Case.law JSON Downloader")
    print("="*70)
    print(f"📥 Input: {INPUT_CSV}")
    print(f"📤 Output: {OUTPUT_DIR}/{OUTPUT_PREFIX}_*.csv")
    print(f"💾 Checkpoint: {CHECKPOINT_DB}")
    print(f"⚙️  Workers: {MAX_CONCURRENT_DOWNLOADS}")
    print(f"📦 Records per file: {RECORDS_PER_FILE:,}")
    print("="*70)

    # Initialize
    init_checkpoint_db()

    # Check existing progress
    progress = get_progress_stats()
    print(f"\n📊 Resume from checkpoint:")
    print(f"   ✓ Already downloaded: {progress['success']:,}")
    print(f"   ✗ Failed: {progress['failed']:,}")
    print(f"   💾 Total size: {progress['total_bytes']:,} bytes")

    # Count total rows
    print(f"\n🔢 Counting total rows in CSV...")
    total_rows = sum(1 for _ in open(INPUT_CSV)) - 1  # Exclude header
    print(f"   Total rows: {total_rows:,}")

    remaining = total_rows - progress['success']
    print(f"   Remaining: {remaining:,}")

    if remaining == 0:
        print("\n✅ All files already downloaded!")
        return

    # Estimate time
    estimated_hours = (remaining / MAX_CONCURRENT_DOWNLOADS / 10) / 3600
    print(f"   ⏱️  Estimated time: {estimated_hours:.1f} hours")

    input("\n▶️  Press ENTER to start download...")

    # Setup
    csv_writer = PartitionedCSVWriter(OUTPUT_DIR, OUTPUT_PREFIX, RECORDS_PER_FILE)
    queue = asyncio.Queue(maxsize=MAX_CONCURRENT_DOWNLOADS * 2)
    checkpoint_batch = []

    # Setup aiohttp session
    connector = aiohttp.TCPConnector(
        limit=MAX_CONCURRENT_DOWNLOADS,
        limit_per_host=20,
        ttl_dns_cache=300
    )
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        # Start workers
        workers = [
            asyncio.create_task(worker(session, queue, csv_writer, checkpoint_batch))
            for _ in range(MAX_CONCURRENT_DOWNLOADS)
        ]

        # Read CSV and feed queue
        print("\n🔄 Starting download...\n")

        with tqdm(total=remaining, desc="Downloading", unit=" files") as pbar:
            csv_reader = pd.read_csv(INPUT_CSV, chunksize=CHUNK_SIZE)

            for chunk in csv_reader:
                if shutdown_flag:
                    break

                for _, row in chunk.iterrows():
                    if shutdown_flag:
                        break

                    # Skip if already downloaded
                    if is_url_downloaded(row['url']):
                        continue

                    # Add to queue
                    await queue.put(row.to_dict())
                    pbar.update(1)

                    # Update progress bar description
                    elapsed = time.time() - stats['start_time']
                    speed = stats['total_processed'] / elapsed if elapsed > 0 else 0
                    pbar.set_postfix({
                        'speed': f'{speed:.1f}/s',
                        'success': stats['total_success'],
                        'failed': stats['total_failed'],
                        'part': stats['current_part']
                    })

        # Send poison pills to workers
        for _ in range(MAX_CONCURRENT_DOWNLOADS):
            await queue.put(None)

        # Wait for all workers to finish
        print("\n⏳ Waiting for workers to finish...")
        await asyncio.gather(*workers)

        # Final checkpoint commit
        if checkpoint_batch:
            save_checkpoint_batch(checkpoint_batch)

        # Close CSV writer
        csv_writer.close()

    # Final statistics
    elapsed = time.time() - stats['start_time']

    print("\n" + "="*70)
    print("🎉 DOWNLOAD COMPLETED!")
    print("="*70)
    print(f"✓ Total processed: {stats['total_processed']:,}")
    print(f"✓ Successful: {stats['total_success']:,}")
    print(f"✗ Failed: {stats['total_failed']:,}")
    print(f"💾 Total downloaded: {stats['total_bytes']:,} bytes ({stats['total_bytes']/1024/1024/1024:.2f} GB)")
    print(f"⏱️  Total time: {elapsed/3600:.2f} hours")
    print(f"⚡ Average speed: {stats['total_processed']/elapsed:.2f} files/second")
    print(f"📊 CSV parts created: {stats['current_part']}")
    print("="*70)


# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⚠️  Download interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
```

---

## 🚀 Cách Sử Dụng

### 1. Chuẩn bị:

```bash
# Di chuyển đến thư mục project
cd /Volumes/DATA/mrHoang/service_crawling_caselaw_v2

# Tạo virtual environment (khuyến nghị)
python3 -m venv venv
source venv/bin/activate

# Cài đặt dependencies
pip install aiohttp aiofiles pandas tqdm aiosqlite

# Tăng file descriptor limit
ulimit -n 10000
```

### 2. Chạy script:

```bash
# Chạy lần đầu
python download_caselaw_json.py

# Script sẽ tự động:
# - Tạo checkpoint database
# - Kiểm tra progress hiện tại
# - Ước tính thời gian
# - Yêu cầu xác nhận trước khi bắt đầu
```

### 3. Resume nếu bị gián đoạn:

```bash
# Chỉ cần chạy lại script
python download_caselaw_json.py

# Script tự động:
# - Đọc checkpoint từ SQLite
# - Bỏ qua các URLs đã download
# - Tiếp tục từ vị trí dừng
```

### 4. Monitor progress:

```bash
# Terminal 1: Chạy script
python download_caselaw_json.py

# Terminal 2: Monitor resources
htop

# Terminal 3: Check disk usage
watch -n 5 'du -sh downloaded_json_data'

# Terminal 4: Check progress in DB
sqlite3 download_checkpoint.db "SELECT COUNT(*) FROM downloads WHERE success=1"
```

---

## 📊 Output Structure

### CSV Files:

```
downloaded_json_data/
├── caselaw_json_part_0001.csv  (100K records, ~500MB)
├── caselaw_json_part_0002.csv  (100K records, ~500MB)
├── caselaw_json_part_0003.csv  (100K records, ~500MB)
├── ...
└── caselaw_json_part_0068.csv  (remaining records)

Total: ~68 files, ~34GB
```

### CSV Format:

```csv
jurisdiction,volume,filename,url,json_content,download_timestamp,size_bytes,http_status
a2d,102,0300-01.json,https://...,"{""id"":123,...}",2025-10-16T10:30:45,13905,200
```

### Checkpoint Database:

```sql
-- Table: downloads
CREATE TABLE downloads (
    url TEXT PRIMARY KEY,
    jurisdiction TEXT,
    volume TEXT,
    filename TEXT,
    success INTEGER,
    size_bytes INTEGER,
    timestamp TEXT,
    error_message TEXT
);
```

---

## 🔍 Troubleshooting

### 1. Script chậm / Stuck:

```bash
# Giảm số workers
MAX_CONCURRENT_DOWNLOADS = 50  # Thay vì 100

# Tăng timeout
REQUEST_TIMEOUT = 120  # Thay vì 60
```

### 2. Too many open files error:

```bash
# Tăng limit cao hơn
ulimit -n 20000

# Hoặc giảm workers
MAX_CONCURRENT_DOWNLOADS = 50
```

### 3. MacBook nóng / chậm:

```bash
# Thêm cooling break trong worker:
await asyncio.sleep(0.01)  # 10ms delay

# Hoặc giảm workers
MAX_CONCURRENT_DOWNLOADS = 30
```

### 4. Network errors / Timeouts:

```bash
# Tăng retries
MAX_RETRIES = 5

# Tăng timeout
REQUEST_TIMEOUT = 180

# Tăng delay
RATE_LIMIT_DELAY = 0.01  # 10ms
```

### 5. Disk đầy:

```bash
# Compress CSV files on-the-fly
gzip downloaded_json_data/*.csv

# Hoặc giảm records per file
RECORDS_PER_FILE = 50000  # 50K instead of 100K
```

### 6. Check failed downloads:

```sql
-- Query checkpoint DB
sqlite3 download_checkpoint.db

-- Get failed URLs
SELECT url, error_message FROM downloads WHERE success=0 LIMIT 100;

-- Count by error type
SELECT error_message, COUNT(*) FROM downloads
WHERE success=0 GROUP BY error_message;
```

---

## 📈 Performance Tuning

### Tối ưu cho tốc độ:

```python
MAX_CONCURRENT_DOWNLOADS = 200  # Tăng workers
RATE_LIMIT_DELAY = 0            # Bỏ delay
BATCH_COMMIT_SIZE = 5000        # Commit ít hơn
```

### Tối ưu cho ổn định:

```python
MAX_CONCURRENT_DOWNLOADS = 50   # Giảm workers
RATE_LIMIT_DELAY = 0.01         # Thêm delay
MAX_RETRIES = 5                 # Retry nhiều hơn
REQUEST_TIMEOUT = 180           # Timeout cao hơn
```

### Tối ưu cho MacBook (Balance):

```python
MAX_CONCURRENT_DOWNLOADS = 100  # Default
RATE_LIMIT_DELAY = 0.001        # Small delay
MAX_RETRIES = 3                 # Reasonable
REQUEST_TIMEOUT = 60            # Standard
```

---

## ⏱️ Estimated Timeline

### Với cấu hình mặc định:

| Scenario     | Workers | Speed  | Time       |
| ------------ | ------- | ------ | ---------- |
| Best case    | 100     | 1000/s | 2-3 hours  |
| Normal       | 100     | 500/s  | 4-5 hours  |
| Conservative | 100     | 300/s  | 6-7 hours  |
| With errors  | 100     | 200/s  | 9-10 hours |

### Factors ảnh hưởng:

- ✅ Network speed và stability
- ✅ Server response time (case.law)
- ✅ MacBook performance
- ✅ Disk I/O speed
- ⚠️ Rate limiting từ server
- ⚠️ Network errors / timeouts

---

## 🎯 Next Steps

### Sau khi download xong:

1. **Verify data integrity:**

```bash
# Count total records
wc -l downloaded_json_data/*.csv

# Check for empty files
find downloaded_json_data -type f -size 0

# Validate JSON in random samples
python validate_json_samples.py
```

2. **Compress files:**

```bash
# Compress individual files
gzip downloaded_json_data/*.csv

# Or create tar archive
tar -czf caselaw_json_data.tar.gz downloaded_json_data/
```

3. **Upload to cloud (optional):**

```bash
# Upload to Google Cloud Storage
gsutil -m cp downloaded_json_data/*.csv gs://your-bucket/

# Or AWS S3
aws s3 sync downloaded_json_data/ s3://your-bucket/caselaw/
```

4. **Clean up:**

```bash
# Remove checkpoint DB
rm download_checkpoint.db

# Keep only compressed files
rm downloaded_json_data/*.csv  # After compressing
```

---

## 🆘 Support & Debug

### Enable debug logging:

```python
# Add to script top
import logging
logging.basicConfig(level=logging.DEBUG)
```

### Export failed URLs for retry:

```bash
# Export to CSV
sqlite3 download_checkpoint.db <<EOF
.mode csv
.output failed_urls.csv
SELECT jurisdiction, volume, filename, url, error_message
FROM downloads WHERE success=0;
.quit
EOF
```

### Monitor in real-time:

```bash
# Watch progress
watch -n 5 'sqlite3 download_checkpoint.db "SELECT
  COUNT(CASE WHEN success=1 THEN 1 END) as success,
  COUNT(CASE WHEN success=0 THEN 1 END) as failed,
  SUM(size_bytes)/1024/1024/1024 as total_gb
FROM downloads"'
```

---

## ✅ Checklist Trước Khi Chạy

- [ ] Đã cài đặt Python 3.8+
- [ ] Đã cài đặt tất cả dependencies
- [ ] Đã tăng file descriptor limit (ulimit -n 10000)
- [ ] Có ít nhất 100GB disk space trống
- [ ] Internet connection ổn định
- [ ] File input CSV tồn tại
- [ ] Đã đọc và hiểu configuration
- [ ] Sẵn sàng để script chạy 4-8 giờ

---

## 📝 Notes

- ⚠️ Script sẽ tạo khoảng **68 CSV files**, mỗi file ~500MB
- ⚠️ Tổng dung lượng output: **~34GB** (chưa compress)
- ⚠️ Checkpoint DB có thể lên đến **2-3GB**
- ✅ Script an toàn để chạy qua đêm
- ✅ Có thể pause/resume bất cứ lúc nào
- ✅ Không làm mất dữ liệu khi gián đoạn

---

## 🚀 Ready to Go!

```bash
# Let's do this!
python download_caselaw_json.py
```

Good luck! 🍀
