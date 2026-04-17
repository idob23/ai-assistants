"""
X431 Launch diagnostic report parser.

Usage:
    parser = X431ReportParser()
    report = parser.fetch_report("https://euait.x431.com/Home/Report/reportDetail/...")
    print(report)

Architecture:
    1. GET the public report URL -> grab HTML + session cookies
    2. Parse HTML for metadata (VIN, model, date, serial) and subsystem refs
    3. For each subsystem, POST to getSubSystemDetail -> JSON with DTCs + data flow
    4. Return a single structured dict
"""

import re
import time
import logging
from dataclasses import dataclass, field, asdict
from typing import Optional
import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)


# ---------- Data model ----------

@dataclass
class FaultCode:
    code: str                    # DTC_645
    description: str             # "CAN Message Information Tachograph"
    status: str                  # "Неправдоподобный." / "Короткое замыкание." / ...
    freeze_frame: list = field(default_factory=list)  # [{title, value, unit}]


@dataclass
class DataFlowItem:
    name: str
    value: str
    unit: str


@dataclass
class Subsystem:
    name: str                    # "TGA (1999-)"
    subsystem_id: str            # "7271812" — Launch internal ID
    fault_count: int
    fault_codes: list = field(default_factory=list)
    data_flow: list = field(default_factory=list)
    ecu_versions: list = field(default_factory=list)


@dataclass
class DiagnosticReport:
    report_code: str             # "X20029091262"
    record_id: str               # "29091262"
    report_type: str             # "X2"
    report_time: str             # unix ts
    vin: str                     # "WMAH54ZZ3CL******" (masked at source)
    make_model: str              # "HD_MAN/"
    year: str
    software_version: str
    scanner_sn: str              # serial of the X431 unit
    workshop: str
    diag_datetime: str           # "03/22/2026 00:54:27"
    summary_items: list = field(default_factory=list)  # top-level problem list
    subsystems: list = field(default_factory=list)
    source_url: str = ""


# ---------- Parser ----------

class X431ReportParser:
    BASE_URL = "https://euait.x431.com"
    DETAIL_ENDPOINT = "/Home/Report/getSubSystemDetail"

    # Regex for the ng-click handler:
    #   ng-click="getDetail(29091262,7271812,'X2',1774140867,$event)"
    GETDETAIL_RE = re.compile(
        r"getDetail\(\s*(\d+)\s*,\s*(\d+)\s*,\s*'([^']+)'\s*,\s*(\d+)",
    )

    DEFAULT_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    def __init__(self, request_timeout: int = 15, rate_limit_delay: float = 0.5):
        self.session = requests.Session()
        self.session.headers.update(self.DEFAULT_HEADERS)
        self.timeout = request_timeout
        self.delay = rate_limit_delay  # pause between subsystem requests

    # --- public API ---

    def fetch_report(self, report_url: str) -> DiagnosticReport:
        log.info("Fetching report page: %s", report_url)
        html = self._get_html(report_url)

        report = self._parse_metadata(html)
        report.source_url = report_url

        subsys_refs = self._extract_subsystem_refs(html)
        log.info("Found %d subsystem(s)", len(subsys_refs))

        for ref in subsys_refs:
            detail = self._fetch_subsystem_detail(
                record_id=ref["record_id"],
                subsystem_id=ref["subsystem_id"],
                report_type=ref["report_type"],
                report_time=ref["report_time"],
                referer=report_url,
            )
            subsys = Subsystem(
                name=ref["name"],
                subsystem_id=ref["subsystem_id"],
                fault_count=detail.get("fault_n", 0),
                fault_codes=[
                    FaultCode(
                        code=f.get("fault_code", ""),
                        description=f.get("fault_description", ""),
                        status=(f.get("fault_status") or "").strip(),
                        freeze_frame=f.get("Freeze", []) or [],
                    )
                    for f in detail.get("fault_code_list", [])
                ],
                data_flow=[
                    DataFlowItem(
                        name=item.get("item_name", ""),
                        value=(item.get("list") or [{}])[0].get("value", ""),
                        unit=item.get("item_unit", ""),
                    )
                    for item in detail.get("data_flow_list", [])
                ],
                ecu_versions=detail.get("ecu_list", []) or [],
            )
            report.subsystems.append(subsys)

            if self.delay:
                time.sleep(self.delay)

        # Populate report_type / report_time from first subsystem ref
        if subsys_refs:
            report.report_type = subsys_refs[0]["report_type"]
            report.report_time = subsys_refs[0]["report_time"]
            report.record_id = subsys_refs[0]["record_id"]

        return report

    # --- internals ---

    def _get_html(self, url: str) -> str:
        r = self.session.get(url, timeout=self.timeout)
        r.raise_for_status()
        return r.text

    def _parse_metadata(self, html: str) -> DiagnosticReport:
        soup = BeautifulSoup(html, "html.parser")

        def field_after_bold(label: str) -> str:
            """Find <b>Label:</b> and return text from the same parent after the bold tag.

            Walks sibling text after <b>, collapsing whitespace. Returns "" for empty fields.
            """
            for b in soup.find_all("b"):
                if label in b.get_text():
                    # Collect all text nodes after the <b> within the same parent
                    parts = []
                    for sibling in b.next_siblings:
                        if hasattr(sibling, "get_text"):
                            parts.append(sibling.get_text(" ", strip=True))
                        else:
                            parts.append(str(sibling).strip())
                    text = " ".join(p for p in parts if p).strip()
                    # Some values have a trailing colon from the label; strip leading punctuation
                    return text.lstrip(" :：").strip()
            return ""

        # Top-level report code: "Кодировка отчёта:X20029091262"
        report_code = ""
        r_num = soup.find("p", class_="r-num")
        if r_num:
            m = re.search(r"(X\d+)", r_num.get_text())
            if m:
                report_code = m.group(1)

        # Summary problem list (red bullets under "Результаты проверки")
        summary = [
            re.sub(r"^\d+\.", "", p.get_text(strip=True)).strip()
            for p in soup.find_all("p", class_="font-color-err")
        ]

        return DiagnosticReport(
            report_code=report_code,
            record_id="",  # filled from subsystem ref
            report_type="",
            report_time="",
            vin=field_after_bold("VIN-код"),
            make_model=field_after_bold("Марка/модель"),
            year=field_after_bold("Год выпуска"),
            software_version=field_after_bold("Версия программного обеспечения"),
            scanner_sn=field_after_bold("Серийный номер"),
            workshop=field_after_bold("Мастерская"),
            diag_datetime=field_after_bold("Время диагностики"),
            summary_items=summary,
        )

    def _extract_subsystem_refs(self, html: str) -> list:
        """
        Each subsystem is rendered as:
          <li ... record_id="..." sub_id="..." sys_name="...">
             <a ... ng-click="getDetail(29091262,7271812,'X2',1774140867,$event)"> ...
        """
        soup = BeautifulSoup(html, "html.parser")
        refs = []
        for li in soup.select("li[record_id][sub_id]"):
            a = li.find("a", {"ng-click": True})
            if not a:
                continue
            m = self.GETDETAIL_RE.search(a["ng-click"])
            if not m:
                continue
            refs.append({
                "record_id": m.group(1),
                "subsystem_id": m.group(2),
                "report_type": m.group(3),
                "report_time": m.group(4),
                "name": li.get("sys_name", "").strip(),
            })
        return refs

    def _fetch_subsystem_detail(
        self,
        record_id: str,
        subsystem_id: str,
        report_type: str,
        report_time: str,
        referer: str,
    ) -> dict:
        url = self.BASE_URL + self.DETAIL_ENDPOINT
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Referer": referer,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        }
        payload = {
            "diagnose_record_id": record_id,
            "diagnose_subsystem_id": subsystem_id,
            "report_type": report_type,
            "report_time": report_time,
        }
        r = self.session.post(url, data=payload, headers=headers, timeout=self.timeout)
        r.raise_for_status()
        js = r.json()
        if js.get("code") != 0:
            log.warning("Non-zero response code: %s", js)
            return {}
        return js.get("data", {}) or {}


# ---------- Demo ----------

if __name__ == "__main__":
    import json
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    url = sys.argv[1] if len(sys.argv) > 1 else (
        "https://euait.x431.com/Home/Report/reportDetail/"
        "diagnose_record_id/953df421ge3bOM3boGKw54oGtZ/report_type/X2/l/ru"
    )

    parser = X431ReportParser()
    report = parser.fetch_report(url)

    print(json.dumps(asdict(report), ensure_ascii=False, indent=2, default=str))
