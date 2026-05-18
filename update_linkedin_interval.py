import sqlite3

db = "data/jobops.db"

con = sqlite3.connect(db)
cur = con.cursor()

cur.execute(
    "UPDATE job_search_sources SET interval_minutes = ? WHERE portal = ? AND enabled = ?",
    (10, "linkedin_selenium", 1)
)

con.commit()

print("Actualizadas:", cur.rowcount)

con.close()