#!/usr/bin/env python3
import sqlite3
import sys
from pathlib import Path

p = Path(sys.argv[1])
con = sqlite3.connect(str(p))
print("tables", [r[0] for r in con.execute("select name from sqlite_master where type='table'")])
try:
    n = con.execute("select count(*) from messages").fetchone()[0]
    print("messages", n)
    print("topics", list(con.execute("select id,name from topics")))
    print("first_ts", list(con.execute("select timestamp from messages order by timestamp limit 3")))
    print("last_ts", list(con.execute("select timestamp from messages order by timestamp desc limit 3")))
    rows = list(
        con.execute(
            "select t.name, count(*) from messages m join topics t on m.topic_id=t.id group by t.name"
        )
    )
    for name, c in rows:
        print(f"  {name}: {c}")
except Exception as e:
    print("err", e)
