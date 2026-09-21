"""output 目录自动归档脚本

功能：将 output/ 下超过保留天数（默认 30 天）的旧产物（汇总 Excel、md 报告、
图表、jsonl）压缩打包到 output/archive/archive_YYYYMMDD.zip，然后删除原文件。
单地区 Excel 由 fast_run.summarize 汇总后自动清理，不在此脚本范围。

用法：
    python scripts/archive_output.py              # 归档 30 天前的文件
    python scripts/archive_output.py 14           # 自定义保留天数
"""
import glob
import os
import shutil
import sys
import zipfile
from datetime import datetime

OUTPUT_DIR = "output"
ARCHIVE_DIR = os.path.join(OUTPUT_DIR, "archive")
DEFAULT_KEEP_DAYS = 30

# 需要归档的产物类型（不归档单地区 Excel，那些已被 summarize 自动清理）
ARCHIVE_PATTERNS = [
    "date_*.xlsx",          # 汇总 Excel（date_YYYY-MM-DD.xlsx）
    "summary_*.md",         # 抓取摘要
    "需求洞察报告_*.md",     # 需求洞察报告
    "date_run_*.jsonl",     # 并行批跑结果
    "charts/*.png",         # 图表
]


def archive_old_files(keep_days=DEFAULT_KEEP_DAYS):
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    now = datetime.now()
    to_archive = []
    for pattern in ARCHIVE_PATTERNS:
        for path in glob.glob(os.path.join(OUTPUT_DIR, pattern)):
            mtime = datetime.fromtimestamp(os.path.getmtime(path))
            age_days = (now - mtime).days
            if age_days > keep_days:
                to_archive.append(path)

    if not to_archive:
        print(f"[archive] 无超过 {keep_days} 天的文件，无需归档")
        return 0

    stamp = now.strftime("%Y%m%d")
    zip_path = os.path.join(ARCHIVE_DIR, f"archive_{stamp}.zip")
    # 追加模式：已存在则追加（同一天多次归档）
    mode = "a" if os.path.exists(zip_path) else "w"
    with zipfile.ZipFile(zip_path, mode, zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(to_archive):
            zf.write(path, arcname=os.path.relpath(path, OUTPUT_DIR))
    for path in to_archive:
        os.remove(path)
    print(f"[archive] 归档 {len(to_archive)} 个文件 -> {zip_path}")
    return len(to_archive)


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_KEEP_DAYS
    archive_old_files(days)
