"""Exporta um recorte do observatory.sqlite3 com apenas os dockets NTIA-2024-0002
e BIS-2024-0047 (mais as tabelas pequenas de resumo de todos os dockets) para
data/mini_observatory.sqlite3. Seguro rodar com o coletor ativo, leitura apenas.
Uso:  python export_mini.py
"""
import sqlite3, os, sys

SRC = os.path.join("data", "observatory.sqlite3")
DST = os.path.join("data", "mini_observatory.sqlite3")
DOCKETS = ("NTIA-2024-0002", "BIS-2024-0047", "FAA-2025-1908")

def main():
    if not os.path.exists(SRC):
        sys.exit(f"nao achei {SRC}; rode a partir de C:\\observatory")
    if os.path.exists(DST):
        os.remove(DST)
    con = sqlite3.connect(DST)
    con.execute(f"ATTACH DATABASE '{SRC}' AS src")
    ph = ",".join("?" * len(DOCKETS))

    filtered = {
        "comments":        f"SELECT * FROM src.comments WHERE docket_id IN ({ph})",
        "comment_details": f"SELECT cd.* FROM src.comment_details cd JOIN src.comments c ON c.comment_id=cd.comment_id WHERE c.docket_id IN ({ph})",
        "texts":           f"SELECT t.* FROM src.texts t JOIN src.comments c ON c.comment_id=t.comment_id WHERE c.docket_id IN ({ph})",
        "documents":       f"SELECT * FROM src.documents WHERE docket_id IN ({ph})",
        "runs":            f"SELECT * FROM src.runs WHERE docket_id IN ({ph})",
        "requests":        f"SELECT r.* FROM src.requests r JOIN src.runs u ON u.run_id=r.run_id WHERE u.docket_id IN ({ph})",
    }
    whole = ["dockets", "clusters", "cluster_members", "audit_costs", "retrieval_status",
             "aggregation_profiles", "posting_rhythm", "posting_batches",
             "collection_cursors", "artifacts", "corrections"]

    for name, q in filtered.items():
        con.execute(f"CREATE TABLE {name} AS {q}", DOCKETS)
        n = con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        print(f"{name}: {n}")
    for name in whole:
        con.execute(f"CREATE TABLE {name} AS SELECT * FROM src.{name}")
        n = con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        print(f"{name}: {n}")

    con.commit()
    con.close()
    print(f"\npronto: {DST} ({os.path.getsize(DST)/1e6:.1f} MB)")

if __name__ == "__main__":
    main()
