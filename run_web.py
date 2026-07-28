import time
import webbrowser
import threading
import uvicorn


def open_browser():
    time.sleep(1.5)
    url = "http://127.0.0.1:8000"
    print(f"\n[Launcher] Opening browser: {url}\n")
    webbrowser.open(url)


def main():
    threading.Thread(target=open_browser, daemon=True).start()
    print("[Launcher] Starting server on http://127.0.0.1:8000 ...")
    uvicorn.run("web_server:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
