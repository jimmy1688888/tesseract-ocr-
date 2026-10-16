# -*- coding: utf-8 -*-
"""一批:一次啟動要處理的那組 docx(以完整路徑記住),連同這次的輸出放在哪裡。

見 CONTEXT.md「一批」與 docs/adr/0009-batch-is-config-results-are-returned.md。
`Batch` 只是設定(從哪讀);處理的結果一律用回傳值往下傳,**不寄放在這裡**。
想把某個結果暫存到 `Batch` 上的時候,先回去讀 ADR-0009。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEFAULT_OUTPUT_DIR = Path("./scan_results")


@dataclass(frozen=True)
class Batch:
    docx_files: tuple[Path, ...]
    source: Path              # 輸入資料夾,或 --file 指定的那份;錯誤訊息用
    single_file: bool = False
    output_dir: Path = DEFAULT_OUTPUT_DIR

    @classmethod
    def from_dir(cls, input_dir: Path, output_dir: Path = DEFAULT_OUTPUT_DIR) -> Batch:
        """資料夾裡要處理的 .docx,已排除 Word 的鎖定檔。

        Word 開著某份文件時會在同目錄產生 `~$檔名.docx`(約 162 bytes,不是 zip)。
        它符合 *.docx,glob 撈得到,而 docx_images.read_images 會在它上面拋
        BadZipFile 讓整批當場掛掉 —— 而觸發條件只是「有人正開著那份檔案在看」。
        """
        return cls(tuple(sorted(p for p in input_dir.glob("*.docx")
                                if not p.name.startswith("~$"))),
                   source=input_dir, output_dir=output_dir)

    @classmethod
    def from_file(cls, path: Path | str,
                  output_dir: Path = DEFAULT_OUTPUT_DIR) -> Batch:
        """`--file` 指定的那一份。存不存在不在這裡判斷,見 problem()。"""
        path = Path(path)
        return cls((path,), source=path, single_file=True, output_dir=output_dir)

    # ── 往哪寫:全部從 output_dir 衍生,換一個輸出目錄就全部跟著換 ──────────

    @property
    def matches_csv(self) -> Path:
        """Tesseract 掃描結果,下一步分流的輸入。"""
        return self.output_dir / "matches.csv"

    @property
    def upload_log(self) -> Path:
        """已寫入 Sheets 的列,本地審計紀錄;也是「已知許可證清單」的來源。"""
        return self.output_dir / "upload_log.csv"

    @property
    def employer_crop_dir(self) -> Path:
        """雇主 ROI 截圖(去紅章前的契約頁裁切),供人工複查 H~O 欄。"""
        return self.output_dir / "employer_crops"

    @property
    def employer_texts_json(self) -> Path:
        """雇主契約頁的 Vision 全文,供離線重放(見 write_employer_texts)。

        這是整條流程唯一花錢買來的東西,而每一次「抽取規則改對了沒」的驗證都要拿
        它當素材——丟掉的話,改一次名稱或地址的解析就得再跑一輪 Vision 才驗得了,
        於是實務上就會變成憑猜測改。存這份等於把「離線重放」變成常態。
        個資考量:同目錄的 employer_crops 裁切圖已含相同內容,存文字不是新的曝險;
        scan_results/ 整個在 .gitignore 內。
        """
        return self.output_dir / "employer_texts.json"

    @property
    def doubt_report_csv(self) -> Path:
        """地址疑慮明細,供人工數誤報率。

        ADR-0003 選擇「寧可多報、不調門檻」,代價是誤報率未知;pipeline 對 Sheets
        只寫不讀,誤報率不會自己回來,得靠這份報表人工數一次。
        """
        return self.output_dir / "address_doubts.csv"

    @property
    def no_hit_review_dir(self) -> Path:
        """全無命中的 docx 保留關鍵頁原圖,供人工比對。"""
        return self.output_dir / "no_hit_review"

    def problem(self) -> str | None:
        """沒東西可處理時說原因;有東西可處理回傳 None。

        preflight 的輸入閘門與 main 都看這一個判斷 —— 兩處各寫一份的話,遲早
        會出現「preflight 說可以、main 說找不到」這種對不起來的訊息。
        """
        if self.single_file:
            if not self.source.is_file():
                return f"找不到檔案：{self.source.resolve()}"
            return None
        if not self.source.is_dir():
            return f"輸入資料夾不存在：{self.source.resolve()}"
        if not self.docx_files:
            return f"輸入資料夾內沒有 .docx：{self.source.resolve()}"
        return None

    def path_of(self, docx_name: str) -> Path | None:
        """依檔名查回這一批裡那份 docx 的完整路徑。

        docx 穿過 matches.csv 時只剩檔名;下游要開檔時跟這裡查,不要拿輸入資料夾
        重拼 —— `--file` 指到別處時,重拼出來的是另一份檔案或根本不存在。
        """
        for p in self.docx_files:
            if p.name == docx_name:
                return p
        return None
