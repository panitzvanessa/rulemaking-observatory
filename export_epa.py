"""Exporta um recorte ENXUTO do docket EPA-HQ-OAR-2025-0194 para
data/epa_slim.sqlite3, sem os JSON brutos, para caber no upload do chat.
Uso:  python export_epa.py   (a partir de C:\\observatory)
"""
import sqlite3, os, sys

SRC = os.path.join("data", "observatory.sqlite3")
DST = os.path.join("data", "epa_slim.sqlite3")
D = "EPA-HQ-OAR-2025-0194"

def main():
    if not os.path.exists(SRC):
        sys.exit("nao achei data/observatory.sqlite3; rode a partir de C:\\observatory")
    if os.path.exists(DST):
        os.remove(DST)
    con = sqlite3.connect(DST)
    con.execute(f"ATTACH DATABASE '{SRC}' AS src")

    con.execute("""CREATE TABLE comments AS
        SELECT comment_id, docket_id, receive_date, posted_date, has_attachment, tracking_number
        FROM src.comments WHERE docket_id = ?""", (D,))
    con.execute("""CREATE TABLE details_slim AS
        SELECT cd.comment_id,
               json_extract(cd.raw_json, '$.attributes.organization')      AS organization,
               json_extract(cd.raw_json, '$.attributes.firstName')         AS first_name,
               json_extract(cd.raw_json, '$.attributes.lastName')          AS last_name,
               json_extract(cd.raw_json, '$.attributes.duplicateComments') AS duplicate_comments,
               json_extract(cd.raw_json, '$.attributes.numItemsReceived')  AS num_items_received,
               json_extract(cd.raw_json, '$.attributes.stateProvinceRegion') AS state,
               json_extract(cd.raw_json, '$.attributes.country')           AS country,
               json_extract(cd.raw_json, '$.attributes.trackingNbr')       AS tracking_nbr
        FROM src.comment_details cd
        JOIN src.comments c ON c.comment_id = cd.comment_id
        WHERE c.docket_id = ?""", (D,))
    con.execute("""CREATE TABLE texts AS
        SELECT t.text_id, t.comment_id, t.source, t.is_placeholder, t.char_len, t.word_len, t.content
        FROM src.texts t JOIN src.comments c ON c.comment_id = t.comment_id
        WHERE c.docket_id = ?""", (D,))
    for name in ("clusters", "cluster_members", "posting_rhythm", "posting_batches",
                 "audit_costs", "aggregation_profiles", "retrieval_status", "runs"):
        con.execute(f"CREATE TABLE {name} AS SELECT * FROM src.{name}")

    con.commit()
    for t in ("comments", "details_slim", "texts"):
        print(t, con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
    con.close()
    print(f"pronto: {DST} ({os.path.getsize(DST)/1e6:.1f} MB)")

if __name__ == "__main__":
    main()
