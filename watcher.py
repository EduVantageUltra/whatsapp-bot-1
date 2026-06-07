import time
import os
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from extract import process_file

WATCH_FOLDER = r"C:\clients-system\files"

SUPPORTED_EXTENSIONS = [
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv"
]

class ClientFileHandler(FileSystemEventHandler):
    
    def __init__(self):
        self.processed_files = set()  # Already processed files track karo
        self.last_event_time = {}     # Last event time track karo
    
    def on_created(self, event):
        if event.is_directory:
            return
        self.handle_file(event.src_path)
    
    def on_moved(self, event):
        if event.is_directory:
            return
        self.handle_file(event.dest_path)
    
    def handle_file(self, file_path):
        ext = os.path.splitext(file_path)[1].lower()
        
        if ext not in SUPPORTED_EXTENSIONS:
            return
        
        # Duplicate event check — same file 2 sec ke andar dobara nahi
        now = time.time()
        last_time = self.last_event_time.get(file_path, 0)
        if now - last_time < 2:
            return
        self.last_event_time[file_path] = now
        
        # Already processed check
        if file_path in self.processed_files:
            print(f"⚠️ Already processed, skip: {os.path.basename(file_path)}")
            return
        
        print(f"\n🔔 Nayi file: {os.path.basename(file_path)}")
        
        # File copy hone ka wait karo
        self.wait_for_file(file_path)
        
        # Process karo
        try:
            process_file(file_path)
            self.processed_files.add(file_path)  # Mark as processed
        except Exception as e:
            print(f"❌ Error: {e}")
    
    def wait_for_file(self, file_path):
        previous_size = -1
        while True:
            try:
                current_size = os.path.getsize(file_path)
                if current_size == previous_size and current_size > 0:
                    time.sleep(1)
                    break
                previous_size = current_size
                time.sleep(0.5)
            except OSError:
                time.sleep(0.5)

def start_watcher():
    if not os.path.exists(WATCH_FOLDER):
        os.makedirs(WATCH_FOLDER)
        print(f"✅ Folder bana diya: {WATCH_FOLDER}")
    
    print("\n" + "="*50)
    print("🚀 CLIENT FILE WATCHER SHURU HO GAYA!")
    print("="*50)
    print(f"📁 Watch folder: {WATCH_FOLDER}")
    print(f"🔄 Jaise hi file aayegi — automatic process hogi!")
    print("⛔ Band karne ke liye: Ctrl+C")
    print("="*50 + "\n")
    
    event_handler = ClientFileHandler()
    observer = Observer()
    observer.schedule(event_handler, WATCH_FOLDER, recursive=False)
    observer.start()
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n⛔ Watcher band ho raha hai...")
        observer.stop()
    
    observer.join()

if __name__ == "__main__":
    start_watcher()