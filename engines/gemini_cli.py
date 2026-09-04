"""透過本機 Gemini CLI（gemini -p ... -o json）無頭執行。
注意：Google 個人帳號免費層已停止支援此 CLI；請改用 API key 認證
（設定 GEMINI_API_KEY 環境變數，或 ~/.gemini/settings.json 的
security.auth.selectedType = "gemini-api-key"）。每次呼叫需啟動 Node，約 5~10 秒。"""
import json
import os
import shutil
import subprocess
import tempfile

from . import AI_PROMPT, EngineResult


class GeminiCliEngine:
    key = "gemini_cli"

    def __init__(self, config):
        self.model = config.get("gemini_cli_model", "")
        self.exe = shutil.which("gemini.cmd") or shutil.which("gemini")

    def translate_image(self, img) -> EngineResult:
        if not self.exe:
            raise RuntimeError("找不到 gemini CLI（npm i -g @google/gemini-cli）。")
        workdir = tempfile.mkdtemp(prefix="translens_")
        path = os.path.join(workdir, "shot.png")
        img.convert("RGB").save(path, format="PNG")
        cmd = [self.exe, "-p", f"@shot.png\n{AI_PROMPT}", "-o", "json",
               "--approval-mode", "plan", "--skip-trust"]
        if self.model:
            cmd += ["-m", self.model]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120, creationflags=flags)
        out = proc.stdout.strip()
        try:
            shutil.rmtree(workdir, ignore_errors=True)
        except OSError:
            pass
        # CLI 會在 JSON 前印警告，找第一個 '{' 起算
        start = out.find("{")
        if start >= 0:
            try:
                data = json.loads(out[start:])
                text = (data.get("response") or "").strip()
                if text:
                    return EngineResult(text, notes=["Gemini CLI"])
            except json.JSONDecodeError:
                pass
        err = (proc.stderr or out).strip()
        for line in err.splitlines():
            if "Error" in line or "error" in line:
                raise RuntimeError(f"Gemini CLI: {line.strip()[:200]}")
        raise RuntimeError(f"Gemini CLI 無回應（exit {proc.returncode}）: {err[:200]}")
