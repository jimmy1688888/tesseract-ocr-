"""python -m permit_lookup [許可證 …]:印名冊索引筆數並查幾張許可證。"""
from .lookup import PermitLookup

if __name__ == "__main__":
    import sys

    pl = PermitLookup()
    print(f"名冊索引筆數:母公司 {len(pl.main_index)}／分公司 {len(pl.branch_index)}"
          f"／電話 {len(pl.phone_index)}")
    for permit in sys.argv[1:] or ["2340", "2639", "0001", "99999"]:
        info = pl.lookup(permit)
        if info:
            print(f"  {permit} → {info['機構名稱']} ｜ {info['機構地址']} ｜ {info['電話']}"
                  f"（有效={info['有效']}）")
        else:
            print(f"  {permit} → 查無,需人工補")
