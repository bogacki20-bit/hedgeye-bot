"""sg_pages: stale Indices pages are dropped and sg_tilt is written only from a page whose price matches (11/20)."""
from tools import sg_pages as sp

REF = {"SPX": 7175.0, "SPY": 715.0, "QQQ": 645.0, "NDX": 26517.0, "RUT": 2655.0, "IWM": 264.0}
TD = "2026-11-19"
MS = 1795046400000          # 2026-11-19 00:00 UTC


class Cur:
    def __init__(self):
        self.sql = []
        self._r = None

    def execute(self, q, a=None):
        self.sql.append((q, a))
        if "FROM sg_index_daily" in q:
            sym = a[0]
            self._r = (REF[sym],) if sym in REF and (len(a) == 1 or a[1] == TD) else None

    def fetchone(self):
        return self._r


class Conn:
    def __init__(self):
        self.c = Cur()

    def cursor(self):
        return self.c

    def commit(self):
        pass


def page(sym, upx):
    pts = [{"epoch_millis": MS - 86400000, "upx": upx * 1.01, "gamma_tilt": 0.5, "delta_tilt": 0.4},
           {"epoch_millis": MS, "upx": upx, "gamma_tilt": 0.6157, "delta_tilt": 0.6316}]
    return {"captured_at": "2026-11-20T21:31:12Z", "stamp": "20261120_1631", "page": "indices", "sym": sym,
            "tab": "Greeks", "states": {"Tilt#0": pts}}


def test_good_page_writes_tilt():
    cn = Conn()
    r = sp._store(cn, page("SPX", 7175), "f.json")
    assert r["series"] == 1 and "stale" not in r
    assert r["tilt"] == {"sym": "SPX", "trade_date": TD, "gamma_tilt": 0.6157}
    assert any("INSERT INTO sg_tilt" in q for q, _ in cn.c.sql)


def test_stale_page_dropped():
    cn = Conn()
    r = sp._store(cn, page("QQQ", 715), "f.json")        # SPY's data under the QQQ label
    assert r["series"] == 0 and r["stale"]["sym"] == "QQQ"
    assert not any("INSERT INTO sg_series" in q or "INSERT INTO sg_tilt" in q for q, _ in cn.c.sql)


def test_iwm_with_rut_data_dropped():
    r = sp._store(Conn(), page("IWM", 2655), "f.json")
    assert r["stale"]["sym"] == "IWM"


def test_non_indices_untouched():
    d = page("SPX", 715) | {"page": "home"}
    r = sp._store(Conn(), d, "f.json")
    assert r["series"] == 1 and "tilt" not in r and "stale" not in r


if __name__ == "__main__":
    for f in [v for k, v in dict(globals()).items() if k.startswith("test_")]:
        f()
    print("ok")
