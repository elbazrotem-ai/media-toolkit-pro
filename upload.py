import subprocess
import sys
import os

# רשימת הקבצים שאתה מרשה להעלות
ALLOWED_FILES = [
    "main.py",
    "requirements.txt",
    ".gitignore",
]

def run_git_commands(message):
    try:
        # 1. בדיקה אילו קבצים מהרשימה באמת קיימים בתיקייה
        existing_files = [f for f in ALLOWED_FILES if os.path.exists(f)]
        
        if not existing_files:
            print("❌ No allowed files found to upload!")
            return

        # 2. הוספת הקבצים הספציפיים בלבד
        print(f"➕ Adding selected files: {', '.join(existing_files)}...")
        # אנחנו מנקים קודם את ה-Stage כדי למנוע שאריות מ-git add . קודם
        subprocess.run(["git", "reset"], check=True, capture_output=True)
        subprocess.run(["git", "add"] + existing_files, check=True)

        # 3. ביצוע קומיט
        print(f"💾 Committing with message: '{message}'")
        subprocess.run(["git", "commit", "-m", message], check=True)

        # 4. דחיפה לענן
        print("🚀 Pushing to GitHub...")
        subprocess.run(["git", "push", "origin", "main"], check=True)

        print("✅ Done! Only allowed files were pushed.")
        
    except subprocess.CalledProcessError as e:
        # אם אין שינויים לבצע להם קומיט, גיט יחזיר שגיאה - נתעלם ממנה באלגנטיות
        if "nothing to commit" in str(e.stdout) or "nothing to commit" in str(e.stderr):
            print("ℹ️ Nothing to commit, working tree clean.")
        else:
            print(f"❌ Error occurred: {e}")

if __name__ == "__main__":
    msg = sys.argv[1] if len(sys.argv) > 1 else "Auto-update selected files"
    run_git_commands(msg)