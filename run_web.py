import os
import time
import webbrowser
import threading
import uvicorn


def open_browser(host: str, port: int):
    if os.getenv("NO_BROWSER", "false").lower() == "true" or os.getenv("RENDER"):
        return
    time.sleep(1.5)
    display_host = "localhost" if host == "0.0.0.0" else host
    url = f"http://{display_host}:{port}"
    print(f"\n[Launcher] Opening browser: {url}\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass


def main():
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", 8000))
    threading.Thread(target=open_browser, args=(host, port), daemon=True).start()
    print(f"[Launcher] Starting server on http://{host}:{port} ...")
    uvicorn.run("web_server:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
