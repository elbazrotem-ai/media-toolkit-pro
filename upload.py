import subprocess
import sys

def run_git_commands(message):
    try:
        # 1. Stage all non-ignored files
        subprocess.run(["git", "add", "."], check=True)

        # 2. Check for staged changes
        status = subprocess.run(["git", "diff", "--cached", "--quiet"], check=False)
        
        # Exit code 0 means no changes to commit
        if status.returncode == 0:
            print("ℹ️ Nothing to commit, working tree clean. Skipping push.")
            return

        # 3. Commit changes
        print(f"💾 Committing: '{message}'")
        subprocess.run(["git", "commit", "-m", message], check=True)

        # 4. Push to remote
        print("🚀 Pushing to GitHub...")
        subprocess.run(["git", "push", "origin", "main"], check=True)
        print("✅ Done!")

    except subprocess.CalledProcessError as e:
        print(f"❌ Git error: {e}")

if __name__ == "__main__":
    msg = sys.argv[1] if len(sys.argv) > 1 else "Auto-update selected files"
    run_git_commands(msg)