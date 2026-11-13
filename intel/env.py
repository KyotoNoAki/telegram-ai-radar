"""本地运行时从仓库根目录的 .env 读取环境变量（.env 已在 .gitignore 里，不会提交）。

已经存在的环境变量优先，不会被 .env 覆盖。GitHub Action 里不需要 .env，密钥来自仓库 Secrets。
"""
import os
from pathlib import Path


def load_dotenv(path):
    path = Path(path)
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip().removeprefix("export ").strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v
