"""output / logs 目录自动归档脚本

功能：
- output/ 下超过保留天数（默认 30 天）的旧产物（汇总 Excel、md 报告、图表、
  jsonl）压缩打包到 output/archive/archive_YYYYMMDD.zip，然后删除原文件。
- logs/ 下超过保留天数的旧日志（date_run_*.log、crawl_*.log、backfill_*.log）
  压缩打包到 logs/archive/archive_YYYYMMDD.zip，然后删除原文件。
- runtime.log 超过阈值（默认 50MB）时轮转压缩，避免单文件无限膨胀。

单地区 Excel 由 fast_run.summarize 汇总后自动清理，不在此脚本范围。

用法：
    python scripts/archive_output.py              # 归档 30 天前的文件
    python scripts/archive_output.py 14           # 自定义保留天数
"""
import glob
import os
import sys
import zipfile
from datetime import datetime

DEFAULT_KEEP_DAYS = 30
RUNTIME_ROTATE_MB = 50  # runtime.log 超过该大小（MB）则轮转压缩

# 各目录下需要归档的文件模式
ARCHIVE_PATTERNS = {
    "output": [
        "date_*.xlsx",          # 汇总 Excel（date_YYYY-MM-DD.xlsx）
        "summary_*.md",         # 抓取摘要
        "需求洞察报告_*.md",     # 需求洞察报告
        "date_run_*.jsonl",     # 并行批跑结果
        "charts/*.png",         # 图表
    ],
    "logs": [
        "date_run_*.log",       # 按日期并行批跑日志
        "crawl_*.log",          # 早期批量爬取日志
        "backfill_*.log",       # 详情补爬日志
    ],
}


def _archive_dir(base_dir, keep_days, stamp):
    archive_dir = os.path.join(base_dir, "archive")
    os.makedirs(archive_dir, exist_ok=True)
    now = datetime.now()
    to_archive = []
    for pattern in ARCHIVE_PATTERNS.get(base_dir, []):
        for path in glob.glob(os.path.join(base_dir, pattern)):
            if os.path.isdir(path):
                continue
            mtime = datetime.fromtimestamp(os.path.getmtime(path))
            age_days = (now - mtime).days
            if age_days > keep_days:
                to_archive.append(path)

    if not to_archive:
        return 0

    zip_path = os.path.join(archive_dir, f"archive_{stamp}.zip")
    mode = "a" if os.path.exists(zip_path) else "w"
    with zipfile.ZipFile(zip_path, mode, zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(to_archive):
            zf.write(path, arcname=os.path.relpath(path, base_dir))
    for path in to_archive:
        try:
            os.remove(path)
        except OSError:
            pass
    print(f"[archive] {base_dir}/ 归档 {len(to_archive)} 个文件 -> {zip_path}")
    return len(to_archive)


def rotate_runtime_log():
    """runtime.log 超过阈值时轮转压缩为 runtime_<时间戳>.log.zip 并清空原文件。"""
    path = "logs/runtime.log"
    if not os.path.exists(path):
        return
    size_mb = os.path.getsize(path) / 1024 / 1024
    if size_mb < RUNTIME_ROTATE_MB:
        return
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_dir = "logs/archive"
    os.makedirs(archive_dir, exist_ok=True)
    zip_path = os.path.join(archive_dir, f"runtime_{stamp}.log.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(path, arcname="runtime.log")
    # 清空原文件而不是删除（保持日志文件持续可写）
    open(path, "w").close()
    print(f"[archive] runtime.log ({size_mb:.0f}MB) 已轮转 -> {zip_path}")


def archive_all(keep_days=DEFAULT_KEEP_DAYS):
    stamp = datetime.now().strftime("%Y%m%d")
    total = 0
    for base in ("output", "logs"):
        total += _archive_dir(base, keep_days, stamp)
    rotate_runtime_log()
    if total == 0:
        print(f"[archive] 无超过 {keep_days} 天的文件，无需归档")


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_KEEP_DAYS
    archive_all(days)
