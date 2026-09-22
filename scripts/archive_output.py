"""output / logs 目录自动归档脚本

功能：
- output/ 下按日期归档：`output/<YYYY-MM-DD>/` 目录（当天全部产物集中存放），
  目录日期早于保留天数（默认 7 天）则整个目录压缩打包到 output/archive/archive_YYYYMMDD.zip，
  然后删除原目录。
- 兼容迁移前的旧平铺文件（散落在 output/ 根目录的 date_*.xlsx、summary_*.md、
  需求洞察报告_*.md、date_run_*.jsonl、charts/*.png），超期同样归档。
- logs/ 下超过保留天数的旧日志（date_run_*.log、crawl_*.log、backfill_*.log）
  压缩打包到 logs/archive/archive_YYYYMMDD.zip，然后删除原文件。
- runtime.log 超过阈值（默认 50MB）时轮转压缩，避免单文件无限膨胀。

用法：
    python scripts/archive_output.py              # 归档 7 天前的文件
    python scripts/archive_output.py 14           # 自定义保留天数
"""
import glob
import os
import re
import sys
import zipfile
from datetime import datetime, timedelta

DEFAULT_KEEP_DAYS = 7  # 保留 7 天，之后自动归档压缩
RUNTIME_ROTATE_MB = 50  # runtime.log 超过该大小（MB）则轮转压缩

DATE_DIR_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')  # output 下按日期归档的目录名

# 根目录下仍需兼容归档的旧平铺文件模式（迁移前的历史产物）
LEGACY_PATTERNS = [
    "date_*.xlsx",          # 汇总 Excel（date_YYYY-MM-DD.xlsx）
    "summary_*.md",         # 抓取摘要
    "需求洞察报告_*.md",     # 需求洞察报告
    "date_run_*.jsonl",     # 并行批跑结果
    "charts/*.png",         # 图表
]


def _archive_paths(paths, base_dir, stamp):
    """把文件列表压缩进 base_dir/archive/archive_<stamp>.zip 后删除原文件。"""
    if not paths:
        return 0
    archive_dir = os.path.join(base_dir, "archive")
    os.makedirs(archive_dir, exist_ok=True)
    zip_path = os.path.join(archive_dir, f"archive_{stamp}.zip")
    mode = "a" if os.path.exists(zip_path) else "w"
    with zipfile.ZipFile(zip_path, mode, zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(paths):
            zf.write(path, arcname=os.path.relpath(path, base_dir))
    for path in paths:
        try:
            if os.path.isdir(path):
                import shutil
                shutil.rmtree(path)
            else:
                os.remove(path)
        except OSError:
            pass
    print(f"[archive] 归档 {len(paths)} 个 -> {zip_path}")
    return len(paths)


def _archive_date_dirs(base_dir, keep_days, stamp):
    """按日期归档：output/<YYYY-MM-DD>/ 整目录超期即压缩删除。"""
    cutoff = (datetime.now() - timedelta(days=keep_days)).date()
    archived = 0
    for path in glob.glob(os.path.join(base_dir, "*")):
        name = os.path.basename(path)
        if not (os.path.isdir(path) and DATE_DIR_RE.match(name)):
            continue
        try:
            d = datetime.strptime(name, "%Y-%m-%d").date()
        except ValueError:
            continue
        if d < cutoff:
            archived += _archive_paths([path], base_dir, stamp)
    return archived


def _archive_legacy(base_dir, keep_days, stamp):
    """归档散落在根目录的旧平铺产物（迁移前的历史文件）。"""
    now = datetime.now()
    to_archive = []
    for pattern in LEGACY_PATTERNS:
        for path in glob.glob(os.path.join(base_dir, pattern)):
            if os.path.isdir(path):
                continue
            mtime = datetime.fromtimestamp(os.path.getmtime(path))
            if (now - mtime).days > keep_days:
                to_archive.append(path)
    return _archive_paths(to_archive, base_dir, stamp)


def _archive_logs(base_dir, keep_days, stamp):
    """归档 logs/ 下超期日志文件。"""
    now = datetime.now()
    to_archive = []
    for pattern in ["date_run_*.log", "crawl_*.log", "backfill_*.log"]:
        for path in glob.glob(os.path.join(base_dir, pattern)):
            if os.path.isdir(path):
                continue
            mtime = datetime.fromtimestamp(os.path.getmtime(path))
            if (now - mtime).days > keep_days:
                to_archive.append(path)
    return _archive_paths(to_archive, base_dir, stamp)


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
    total += _archive_date_dirs("output", keep_days, stamp)
    total += _archive_legacy("output", keep_days, stamp)
    total += _archive_logs("logs", keep_days, stamp)
    rotate_runtime_log()
    if total == 0:
        print(f"[archive] 无超过 {keep_days} 天的文件，无需归档")


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_KEEP_DAYS
    archive_all(days)
