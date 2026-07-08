"""调试 copy_segment 外键问题"""
import sys, os, tempfile, shutil
sys.path.insert(0, os.path.abspath('.'))

DB_PATH = "data/railway.db"
tmp_dir = tempfile.mkdtemp()
tmp_db = os.path.join(tmp_dir, "test.db")
shutil.copy2(DB_PATH, tmp_db)

from src.track.editor import TrackEditor

try:
    editor = TrackEditor(tmp_db)
    editor.conn.execute("PRAGMA foreign_keys = ON")

    src = editor.get_segment(1)
    print(f"Segment 1 keys: {list(src.keys())}")

    editor.add_segment(9999, src["length"])
    print("PASS: add_segment")
    editor.conn.commit()

    # 复制限速
    for sl in editor.list_speed_limits(1):
        print(f"  speed_limit: seg={sl['seg_id']} range={sl['start_offset']}-{sl['end_offset']} val={sl['speed_limit']}")
        editor.add_speed_limit(9999, sl["start_offset"], sl["end_offset"], sl["speed_limit"])
    editor.conn.commit()
    print("PASS: copy speed_limits")

    # 复制坡度
    for g in editor.list_gradients(1):
        print(f"  gradient: seg={g['seg_id']} range={g['start_offset']}-{g['end_offset']} val={g['gradient']}")
        editor.add_gradient(9999, g["start_offset"], g["end_offset"], g["gradient"], g.get("direction", ""))
    editor.conn.commit()
    print("PASS: copy gradients")

    editor.close()
    print("\nAll OK!")
except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()
finally:
    shutil.rmtree(tmp_dir, ignore_errors=True)
