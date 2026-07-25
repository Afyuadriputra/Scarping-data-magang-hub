from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin

import pandas as pd
from playwright.sync_api import (
    Browser,
    BrowserContext,
    Locator,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)


# ============================================================
# KONFIGURASI UTAMA
# ============================================================

BASE_URL = "https://maganghub.kemnaker.go.id"
LIST_PATH = "/magang-nasional/lowongan"

OUTPUT_DIR = Path(__file__).resolve().parent / "hasil_scraping"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# True  = browser tidak terlihat
# False = browser terlihat
HEADLESS = False

PAGE_TIMEOUT_MS = 120_000
DEFAULT_TIMEOUT_MS = 30_000
PAGE_CHANGE_TIMEOUT_MS = 20_000

REQUEST_DELAY_SECONDS = 1.5
MAX_PAGINATION_ATTEMPTS = 30
MAX_NEXT_CLICK_RETRIES = 4
MAX_DETAIL_RETRIES = 3

# CSV final hanya dibuat jika seluruh URL berhasil dikumpulkan.
REQUIRE_COMPLETE_LIST = True

# Simpan checkpoint setiap 5 detail.
CHECKPOINT_INTERVAL = 5


# ============================================================
# KONFIGURASI FILTER
# ============================================================

CITY_NAME = "Kota Pekanbaru"
CITY_ID = "49e68da2-eaa7-401c-b4e4-0feaf679287f"

EDUCATION_LEVEL_ID = "bachelor"
EDUCATION_LEVEL_LABEL = "Sarjana"

STUDY_PROGRAMS = [
    {
        "id": "246e6e8a-413d-4dff-9d82-161b6222f8d0",
        "label": "Ilmu Komputer",
    },
    {
        "id": "8bdce5f1-553c-40b4-a5f5-9e5ff9387b60",
        "label": "Sistem Informasi",
    },
    {
        "id": "ae73f44b-835b-498b-a10a-95eaa32fbe0b",
        "label": "Sistem Informasi",
    },
    {
        "id": "03b570a2-c678-4413-aa47-bfed8308fac5",
        "label": "Teknik Informatika",
    },
]


def build_filter_params() -> dict[str, str]:
    """
    Membuat parameter URL filter MagangHub.
    """
    params: dict[str, str] = {
        "keyword": "",
        "education_level[0][id]": EDUCATION_LEVEL_ID,
        "education_level[0][label]": EDUCATION_LEVEL_LABEL,
        "city_id[0][id]": CITY_ID,
        "city_id[0][label]": CITY_NAME,
        "sort": "most_applicants",
    }

    for index, program in enumerate(STUDY_PROGRAMS):
        params[f"study_program[{index}][id]"] = program["id"]
        params[f"study_program[{index}][label]"] = program["label"]

    return params


FILTER_PARAMS = build_filter_params()

LIST_URL = (
    f"{BASE_URL}{LIST_PATH}?"
    f"{urlencode(FILTER_PARAMS)}"
)


# ============================================================
# MODEL DATA KARTU
# ============================================================

@dataclass
class CardData:
    url: str
    card_text: str | None = None
    title_card: str | None = None
    organizer_card: str | None = None
    study_program_card: str | None = None
    location_card: str | None = None
    education_card: str | None = None
    workdays_card: int | None = None
    quota_card: int | None = None
    applicants_card: int | None = None
    holidays_card: str | None = None


# ============================================================
# VALIDASI
# ============================================================

def validate_configuration() -> None:
    """
    Memastikan konfigurasi filter valid.
    """
    if not CITY_NAME.strip():
        raise ValueError("CITY_NAME tidak boleh kosong.")

    uuid_pattern = re.compile(
        r"^[0-9a-f]{8}-"
        r"[0-9a-f]{4}-"
        r"[0-9a-f]{4}-"
        r"[0-9a-f]{4}-"
        r"[0-9a-f]{12}$",
        flags=re.IGNORECASE,
    )

    if not uuid_pattern.fullmatch(CITY_ID):
        raise ValueError(
            f"CITY_ID tidak valid: {CITY_ID}"
        )

    if not STUDY_PROGRAMS:
        raise ValueError(
            "Daftar program studi tidak boleh kosong."
        )

    for index, program in enumerate(STUDY_PROGRAMS):
        if not program.get("id"):
            raise ValueError(
                f"ID program studi indeks {index} kosong."
            )

        if not program.get("label"):
            raise ValueError(
                f"Label program studi indeks {index} kosong."
            )


# ============================================================
# UTILITAS TEKS
# ============================================================

def clean_text(value: Any) -> str | None:
    """
    Membersihkan spasi dan baris kosong berlebihan.
    """
    if value is None:
        return None

    text = re.sub(
        r"\s+",
        " ",
        str(value),
    ).strip()

    return text or None


def extract_int(
    text: str | None,
    pattern: str,
) -> int | None:
    """
    Mengambil bilangan bulat pertama berdasarkan regex.
    """
    if not text:
        return None

    match = re.search(
        pattern,
        text,
        flags=re.IGNORECASE,
    )

    if not match:
        return None

    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


def extract_section(
    text: str | None,
    start_heading: str,
    end_headings: list[str],
) -> str | None:
    """
    Mengambil teks di antara sebuah judul dan judul berikutnya.
    """
    if not text:
        return None

    lower_text = text.lower()
    start_position = lower_text.find(
        start_heading.lower()
    )

    if start_position == -1:
        return None

    content_start = (
        start_position
        + len(start_heading)
    )

    remaining = text[content_start:]
    remaining_lower = remaining.lower()
    content_end = len(remaining)

    for heading in end_headings:
        position = remaining_lower.find(
            heading.lower()
        )

        if 0 <= position < content_end:
            content_end = position

    return clean_text(
        remaining[:content_end]
    )


def first_matching_text(
    page: Page,
    selectors: list[str],
) -> str | None:
    """
    Mengambil teks pertama yang tidak kosong.
    """
    for selector in selectors:
        locator = page.locator(selector)

        try:
            count = locator.count()
        except Exception:
            continue

        for index in range(min(count, 5)):
            try:
                text = clean_text(
                    locator.nth(index).inner_text(
                        timeout=3_000
                    )
                )
            except Exception:
                continue

            if text:
                return text

    return None


def extract_location(
    text: str | None,
) -> str | None:
    """
    Mengambil nama kota atau kabupaten.
    """
    if not text:
        return None

    match = re.search(
        r"\b(Kota|Kabupaten)\s+"
        r"[A-Za-zÀ-ÿ0-9 .,'’()/-]+?"
        r"(?=\s+(?:"
        r"Diploma|Sarjana|Profesi|"
        r"\d+\s*hari|Kuota|Pelamar|"
        r"Hari Libur|Program studi|"
        r"Tingkat pendidikan|"
        r"Deskripsi Lowongan|$"
        r"))",
        text,
        flags=re.IGNORECASE,
    )

    if not match:
        return None

    location = clean_text(
        match.group(0)
    )

    if not location:
        return None

    # Memperbaiki hasil seperti:
    # Kota Pekanbaru Kota Pekanbaru
    duplicate_city_pattern = re.compile(
        rf"^(?:{re.escape(CITY_NAME)}\s+)+"
        rf"{re.escape(CITY_NAME)}$",
        flags=re.IGNORECASE,
    )

    if duplicate_city_pattern.match(location):
        return CITY_NAME

    return location


def extract_education(
    text: str | None,
) -> str | None:
    """
    Mengambil jenjang pendidikan.
    """
    if not text:
        return None

    levels = [
        "Diploma",
        "Sarjana",
        "Profesi",
    ]

    found = [
        level
        for level in levels
        if re.search(
            rf"\b{re.escape(level)}\b",
            text,
            flags=re.IGNORECASE,
        )
    ]

    if not found:
        return None

    return ", ".join(
        dict.fromkeys(found)
    )


def competition_category(
    ratio: float | None,
) -> str | None:
    """
    Mengelompokkan tingkat persaingan.
    """
    if ratio is None or pd.isna(ratio):
        return None

    if ratio <= 5:
        return "Relatif rendah"

    if ratio <= 10:
        return "Sedang"

    if ratio <= 20:
        return "Tinggi"

    return "Sangat tinggi"


def title_from_slug(url: str) -> str:
    """
    Membuat judul dari slug URL.
    """
    slug = url.rstrip("/").split("/")[-1]

    slug = re.sub(
        r"-[0-9a-f]{8}"
        r"-[0-9a-f]{4}"
        r"-[0-9a-f]{4}"
        r"-[0-9a-f]{4}"
        r"-[0-9a-f]{12}$",
        "",
        slug,
        flags=re.IGNORECASE,
    )

    return slug.replace(
        "-",
        " ",
    ).title()


def slugify_filename(value: str) -> str:
    """
    Mengubah teks menjadi nama file yang aman.
    """
    value = value.lower().strip()

    value = re.sub(
        r"[^a-z0-9]+",
        "_",
        value,
    )

    return value.strip("_")


def normalize_detail_url(
    href: str | None,
) -> str | None:
    """
    Menormalisasi URL halaman detail lowongan.
    """
    if not href:
        return None

    absolute_url = urljoin(
        BASE_URL,
        href,
    )

    normalized_url = (
        absolute_url
        .split("#")[0]
        .split("?")[0]
        .rstrip("/")
    )

    if not re.search(
        r"/magang-nasional/lowongan/[^/?#]+$",
        normalized_url,
        flags=re.IGNORECASE,
    ):
        return None

    return normalized_url


# ============================================================
# PARSING KARTU
# ============================================================

def get_card_text(
    link: Locator,
) -> str | None:
    """
    Mengambil teks kartu dengan beberapa fallback.
    """
    try:
        direct_text = clean_text(
            link.inner_text(timeout=2_000)
        )

        if direct_text and len(direct_text) > 10:
            return direct_text
    except Exception:
        pass

    parent_selectors = [
        "xpath=ancestor::article[1]",
        "xpath=ancestor::li[1]",
        (
            "xpath=ancestor::div["
            "contains(@class, 'card')][1]"
        ),
        "xpath=..",
    ]

    for selector in parent_selectors:
        try:
            text = clean_text(
                link.locator(selector).inner_text(
                    timeout=2_000
                )
            )

            if text:
                return text
        except Exception:
            continue

    return None


def parse_card_text(
    url: str,
    card_text: str | None,
) -> CardData:
    """
    Mengambil data dasar dari teks kartu.
    """
    text = clean_text(card_text) or ""

    quota = extract_int(
        text,
        r"Kuota\s*:?\s*(\d+)",
    )

    applicants = extract_int(
        text,
        r"Pelamar\s*:?\s*(\d+)",
    )

    workdays = extract_int(
        text,
        r"(\d+)\s*hari\s*/?\s*minggu",
    )

    holidays = None

    holiday_match = re.search(
        r"Hari Libur\s+(.+?)"
        r"(?=\s+(?:Kuota|Pelamar|$))",
        text,
        flags=re.IGNORECASE,
    )

    if holiday_match:
        holidays = clean_text(
            holiday_match.group(1)
        )

    title = None

    if text:
        city_pattern = re.escape(
            CITY_NAME
        )

        title_part = re.split(
            rf"\s+(?={city_pattern}\b)",
            text,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]

        title = clean_text(
            title_part
        )

    if not title:
        title = title_from_slug(url)

    return CardData(
        url=url,
        card_text=text or None,
        title_card=title,
        location_card=extract_location(text),
        education_card=extract_education(text),
        workdays_card=workdays,
        quota_card=quota,
        applicants_card=applicants,
        holidays_card=holidays,
    )


# ============================================================
# INFORMASI JUMLAH LOWONGAN
# ============================================================

def get_total_listings(
    page_text: str,
) -> int | None:
    """
    Membaca total lowongan dari beberapa pola teks.
    """
    patterns = [
        r"Ditemukan\s+(\d+)\s+lowongan",
        (
            r"Menampilkan\s+\d+"
            r"\s+dari\s+(\d+)"
            r"\s+lowongan"
        ),
        r"Total\s+(\d+)\s+lowongan",
    ]

    for pattern in patterns:
        total = extract_int(
            page_text,
            pattern,
        )

        if total is not None:
            return total

    return None


def get_display_progress(
    page_text: str,
) -> tuple[int | None, int | None]:
    """
    Membaca teks:
    Menampilkan 18 dari 26 lowongan.
    """
    match = re.search(
        r"Menampilkan\s+(\d+)"
        r"\s+dari\s+(\d+)"
        r"\s+lowongan",
        page_text,
        flags=re.IGNORECASE,
    )

    if not match:
        return None, None

    return (
        int(match.group(1)),
        int(match.group(2)),
    )


def collect_current_page_cards(
    page: Page,
) -> list[CardData]:
    """
    Mengambil kartu pada halaman pagination yang sedang aktif.
    """
    links = page.locator(
        'a[href*="/magang-nasional/lowongan/"]'
    )

    cards_by_url: dict[str, CardData] = {}

    for index in range(links.count()):
        link = links.nth(index)

        href = link.get_attribute("href")
        absolute_url = normalize_detail_url(href)

        if not absolute_url:
            continue

        if absolute_url in cards_by_url:
            continue

        card_text = get_card_text(link)

        cards_by_url[absolute_url] = (
            parse_card_text(
                absolute_url,
                card_text,
            )
        )

    return list(
        cards_by_url.values()
    )


# ============================================================
# PAGINATION
# ============================================================

def scroll_to_bottom(page: Page) -> None:
    """
    Scroll ke bagian bawah halaman.
    """
    page.keyboard.press("End")
    page.wait_for_timeout(500)

    page.evaluate(
        """
        () => {
            window.scrollTo({
                top: document.documentElement.scrollHeight,
                behavior: "instant"
            });

            const elements = [
                ...document.querySelectorAll("*")
            ];

            for (const element of elements) {
                const style =
                    getComputedStyle(element);

                const isScrollable = [
                    "auto",
                    "scroll"
                ].includes(style.overflowY);

                if (
                    isScrollable
                    && element.scrollHeight
                    > element.clientHeight
                ) {
                    element.scrollTop =
                        element.scrollHeight;
                }
            }
        }
        """
    )

    page.wait_for_timeout(1_500)


def locator_is_usable(
    locator: Locator,
) -> bool:
    """
    Memastikan tombol terlihat dan aktif.
    """
    try:
        if not locator.is_visible():
            return False

        disabled = locator.get_attribute(
            "disabled"
        )

        aria_disabled = locator.get_attribute(
            "aria-disabled"
        )

        class_name = (
            locator.get_attribute("class")
            or ""
        ).lower()

        if disabled is not None:
            return False

        if aria_disabled == "true":
            return False

        # Jangan memakai `"disabled" in class_name`.
        # Tailwind menaruh variant seperti
        # "disabled:pointer-events-none" pada tombol yang
        # masih aktif. Hanya token class "disabled" yang
        # benar-benar menandakan status nonaktif.
        if re.search(
            r"(?:^|\s)disabled(?:\s|$)",
            class_name,
        ):
            return False

        return True

    except Exception:
        return False


def find_next_page_locator(
    page: Page,
) -> Locator | None:
    """
    Mencari tombol halaman berikutnya.
    """
    # Pagination MagangHub saat ini memakai elemen <a>
    # tanpa href dengan aria-label="Go to next page".
    # Selector eksplisit harus diperiksa lebih dahulu agar
    # tombol tampilan Grid/List tidak ikut terpilih.
    explicit_next = page.locator(
        'a[aria-label="Go to next page" i], '
        'button[aria-label="Go to next page" i], '
        'a[aria-label="Next page" i], '
        'button[aria-label="Next page" i], '
        'a[aria-label="Halaman berikutnya" i], '
        'button[aria-label="Halaman berikutnya" i]'
    )

    try:
        for index in range(explicit_next.count()):
            candidate = explicit_next.nth(index)

            if locator_is_usable(candidate):
                return candidate
    except Exception:
        pass

    text_pattern = re.compile(
        r"(?:"
        r"muat.*lebih.*banyak|"
        r"lihat.*lebih.*banyak|"
        r"tampilkan.*lebih.*banyak|"
        r"lihat.*lowongan.*lain|"
        r"tampilkan.*lowongan.*lain|"
        r"lowongan.*lainnya|"
        r"load.*more|"
        r"berikutnya|"
        r"selanjutnya|"
        r"next"
        r")",
        flags=re.IGNORECASE,
    )

    candidates = [
        page.get_by_role(
            "button",
            name=text_pattern,
        ),
        page.get_by_role(
            "link",
            name=text_pattern,
        ),
        page.locator(
            "button"
        ).filter(
            has_text=text_pattern
        ),
        page.locator(
            "a"
        ).filter(
            has_text=text_pattern
        ),
        page.locator(
            '[role="button"]'
        ).filter(
            has_text=text_pattern
        ),
        page.locator(
            'button[aria-label*="next" i], '
            'a[aria-label*="next" i], '
            'button[title*="next" i], '
            'a[title*="next" i], '
            'button[aria-label*="berikut" i], '
            'a[aria-label*="berikut" i], '
            '[data-testid*="next" i], '
            '[data-testid*="load" i], '
            '[class*="load-more" i], '
            '[class*="pagination-next" i]'
        ),
    ]

    for candidate_group in candidates:
        try:
            count = candidate_group.count()
        except Exception:
            continue

        for index in range(count):
            candidate = candidate_group.nth(index)

            if locator_is_usable(candidate):
                return candidate

    # Fallback: cari tombol setelah teks progres.
    progress_elements = page.get_by_text(
        re.compile(
            r"Menampilkan\s+\d+"
            r"\s+dari\s+\d+"
            r"\s+lowongan",
            flags=re.IGNORECASE,
        )
    )

    try:
        progress_count = (
            progress_elements.count()
        )
    except Exception:
        progress_count = 0

    for index in range(progress_count):
        progress = progress_elements.nth(index)

        fallback_candidates = [
            progress.locator(
                "xpath=following::button[1]"
            ),
            progress.locator(
                "xpath=following::a[1]"
            ),
            progress.locator(
                (
                    "xpath=following::*"
                    "[@role='button'][1]"
                )
            ),
        ]

        for candidate in fallback_candidates:
            try:
                if (
                    candidate.count() > 0
                    and locator_is_usable(
                        candidate.first
                    )
                ):
                    return candidate.first
            except Exception:
                continue

    return None


def click_next_page_javascript(
    page: Page,
) -> bool:
    """
    Fallback JavaScript untuk menekan pagination.
    """
    try:
        clicked = page.evaluate(
            r"""
            () => {
                const normalize = value =>
                    (value || "")
                        .replace(/\s+/g, " ")
                        .trim()
                        .toLowerCase();

                const isVisible = element => {
                    const style =
                        getComputedStyle(element);

                    const rect =
                        element.getBoundingClientRect();

                    return (
                        style.display !== "none"
                        && style.visibility !== "hidden"
                        && Number(
                            style.opacity || 1
                        ) > 0
                        && rect.width > 0
                        && rect.height > 0
                    );
                };

                const isDisabled = element => {
                    const className =
                        String(
                            element.className || ""
                        ).toLowerCase();

                    return (
                        element.disabled
                        || element.getAttribute(
                            "aria-disabled"
                        ) === "true"
                        || /(?:^|\s)disabled(?:\s|$)/
                            .test(className)
                    );
                };

                const patterns = [
                    /muat.*lebih.*banyak/i,
                    /lihat.*lebih.*banyak/i,
                    /tampilkan.*lebih.*banyak/i,
                    /lihat.*lowongan.*lain/i,
                    /tampilkan.*lowongan.*lain/i,
                    /lowongan.*lainnya/i,
                    /load.*more/i,
                    /berikutnya/i,
                    /selanjutnya/i,
                    /^next$/i
                ];

                const elements = [
                    ...document.querySelectorAll(
                        "button, a, [role='button']"
                    )
                ];

                const matching =
                    elements.filter(element => {
                        if (
                            !isVisible(element)
                            || isDisabled(element)
                        ) {
                            return false;
                        }

                        const values = [
                            normalize(
                                element.innerText
                            ),
                            normalize(
                                element.getAttribute(
                                    "aria-label"
                                )
                            ),
                            normalize(
                                element.getAttribute(
                                    "title"
                                )
                            ),
                            normalize(
                                element.getAttribute(
                                    "data-testid"
                                )
                            )
                        ];

                        return values.some(value =>
                            patterns.some(pattern =>
                                pattern.test(value)
                            )
                        );
                    });

                if (matching.length > 0) {
                    const target =
                        matching[
                            matching.length - 1
                        ];

                    target.scrollIntoView({
                        block: "center"
                    });

                    target.click();
                    return true;
                }

                const progressPattern =
                    /menampilkan\s+\d+\s+dari\s+\d+\s+lowongan/i;

                const allElements = [
                    ...document.querySelectorAll(
                        "body *"
                    )
                ];

                const progressElement =
                    allElements.find(element =>
                        progressPattern.test(
                            normalize(
                                element.innerText
                            )
                        )
                        && element.children.length <= 3
                    );

                if (!progressElement) {
                    return false;
                }

                let parent =
                    progressElement.parentElement;

                for (
                    let level = 0;
                    parent && level < 6;
                    level += 1
                ) {
                    const clickable = [
                        ...parent.querySelectorAll(
                            "button, a, [role='button']"
                        )
                    ].filter(element =>
                        isVisible(element)
                        && !isDisabled(element)
                    );

                    if (clickable.length > 0) {
                        const target =
                            clickable[
                                clickable.length - 1
                            ];

                        target.scrollIntoView({
                            block: "center"
                        });

                        target.click();
                        return true;
                    }

                    parent =
                        parent.parentElement;
                }

                return false;
            }
            """
        )

        return bool(clicked)

    except Exception:
        return False


def click_next_page(page: Page) -> bool:
    """
    Menekan tombol pagination.
    """
    next_locator = find_next_page_locator(page)

    if next_locator is not None:
        try:
            next_locator.scroll_into_view_if_needed()
            page.wait_for_timeout(500)

            try:
                next_locator.click(
                    timeout=7_500
                )
            except Exception:
                next_locator.evaluate(
                    "(element) => element.click()"
                )

            return True
        except Exception:
            pass

    return click_next_page_javascript(page)


def wait_until_page_changes(
    page: Page,
    previous_urls: set[str],
) -> bool:
    """
    Menunggu hingga kumpulan URL halaman berubah.
    """
    previous_url_list = sorted(
        previous_urls
    )

    try:
        page.wait_for_function(
            r"""
            previousUrls => {
                const links = [
                    ...document.querySelectorAll(
                        'a[href*="/magang-nasional/lowongan/"]'
                    )
                ];

                const currentUrls = [
                    ...new Set(
                        links
                            .map(link =>
                                link.href
                                    .split("#")[0]
                                    .split("?")[0]
                                    .replace(/\/$/, "")
                            )
                            .filter(url =>
                                /\/magang-nasional\/lowongan\/[^/?#]+$/
                                    .test(url)
                            )
                    )
                ].sort();

                if (currentUrls.length === 0) {
                    return false;
                }

                if (
                    currentUrls.length
                    !== previousUrls.length
                ) {
                    return true;
                }

                return currentUrls.some(
                    (url, index) =>
                        url !== previousUrls[index]
                );
            }
            """,
            arg=previous_url_list,
            timeout=PAGE_CHANGE_TIMEOUT_MS,
        )

        page.wait_for_timeout(1_000)
        return True

    except PlaywrightTimeoutError:
        return False


def print_pagination_candidates(
    page: Page,
) -> None:
    """
    Menampilkan tombol/link yang terlihat untuk diagnosis.
    """
    candidates = page.locator(
        "button, a, [role='button']"
    )

    print()
    print("KANDIDAT PAGINATION YANG TERLIHAT")
    print("-" * 75)

    printed = 0

    for index in range(candidates.count()):
        candidate = candidates.nth(index)

        try:
            if not candidate.is_visible():
                continue

            text = clean_text(
                candidate.inner_text(
                    timeout=1_000
                )
            )

            aria_label = clean_text(
                candidate.get_attribute(
                    "aria-label"
                )
            )

            title = clean_text(
                candidate.get_attribute(
                    "title"
                )
            )

            if not any(
                [text, aria_label, title]
            ):
                continue

            print(
                f"[{index}] "
                f"text={text!r} | "
                f"aria={aria_label!r} | "
                f"title={title!r}"
            )

            printed += 1

            if printed >= 30:
                break

        except Exception:
            continue

    print("-" * 75)
    print()


# ============================================================
# SCRAPING SEMUA HALAMAN DAFTAR
# ============================================================

def collect_listing_cards(
    page: Page,
) -> tuple[list[CardData], int | None]:
    """
    Mengumpulkan semua kartu dari seluruh halaman.
    """
    print("=" * 75)
    print("MEMBUKA SEMUA HALAMAN LOWONGAN")
    print("=" * 75)
    print(f"Kota : {CITY_NAME}")
    print(f"URL  : {LIST_URL}")
    print()

    page.goto(
        LIST_URL,
        wait_until="domcontentloaded",
        timeout=PAGE_TIMEOUT_MS,
    )

    page.wait_for_timeout(5_000)

    initial_text = clean_text(
        page.locator("body").inner_text()
    ) or ""

    total_expected = get_total_listings(
        initial_text
    )

    displayed, progress_total = (
        get_display_progress(initial_text)
    )

    if progress_total is not None:
        total_expected = progress_total

    print(
        "Jumlah lowongan menurut website: "
        f"{total_expected}"
    )

    if displayed is not None:
        print(
            f"Data pada halaman awal: {displayed}"
        )

    all_cards: dict[str, CardData] = {}
    visited_page_signatures: set[
        tuple[str, ...]
    ] = set()

    for page_number in range(
        1,
        MAX_PAGINATION_ATTEMPTS + 1,
    ):
        page.wait_for_timeout(1_000)

        current_cards = (
            collect_current_page_cards(page)
        )

        current_urls = {
            card.url
            for card in current_cards
        }

        if not current_urls:
            raise RuntimeError(
                f"Halaman {page_number} "
                "tidak memiliki URL detail."
            )

        signature = tuple(
            sorted(current_urls)
        )

        before_count = len(all_cards)

        for card in current_cards:
            all_cards.setdefault(
                card.url,
                card,
            )

        new_count = (
            len(all_cards)
            - before_count
        )

        print(
            f"Halaman {page_number}: "
            f"{len(current_urls)} data | "
            f"Baru: {new_count} | "
            f"Total: {len(all_cards)}"
            + (
                f"/{total_expected}"
                if total_expected is not None
                else ""
            )
        )

        if (
            total_expected is not None
            and len(all_cards) >= total_expected
        ):
            break

        if signature in visited_page_signatures:
            raise RuntimeError(
                "Halaman pagination yang sama "
                "muncul kembali sebelum seluruh "
                "data terkumpul."
            )

        visited_page_signatures.add(
            signature
        )

        changed = False

        for click_attempt in range(
            1,
            MAX_NEXT_CLICK_RETRIES + 1,
        ):
            scroll_to_bottom(page)

            clicked = click_next_page(page)

            if not clicked:
                if click_attempt == 1:
                    print_pagination_candidates(page)

                raise RuntimeError(
                    "Tombol halaman berikutnya "
                    "tidak ditemukan."
                )

            print(
                "  Tombol halaman berikutnya ditekan "
                f"(percobaan {click_attempt}/"
                f"{MAX_NEXT_CLICK_RETRIES})."
            )

            changed = wait_until_page_changes(
                page,
                current_urls,
            )

            if changed:
                break

            print(
                "  Isi belum berubah; mencoba "
                "tombol halaman berikutnya lagi."
            )

        if not changed:
            print_pagination_candidates(page)

            raise RuntimeError(
                "Isi halaman tidak berubah setelah "
                f"{MAX_NEXT_CLICK_RETRIES} percobaan "
                "menekan tombol halaman berikutnya."
            )

    cards = list(
        all_cards.values()
    )

    print()
    print(
        "Total URL unik ditemukan: "
        f"{len(cards)}"
    )

    if total_expected is not None:
        if len(cards) < total_expected:
            missing = (
                total_expected
                - len(cards)
            )

            message = (
                f"Data belum lengkap. "
                f"Total website: {total_expected}, "
                f"terkumpul: {len(cards)}, "
                f"kurang: {missing}."
            )

            if REQUIRE_COMPLETE_LIST:
                raise RuntimeError(message)

            print(
                f"PERINGATAN: {message}"
            )

        elif len(cards) == total_expected:
            print(
                f"Semua {total_expected} lowongan "
                "berhasil dikumpulkan."
            )

        else:
            print(
                "PERINGATAN: jumlah URL unik "
                "lebih besar daripada total website."
            )

    print()

    return cards, total_expected


# ============================================================
# SCRAPING DETAIL
# ============================================================

def scrape_detail_once(
    page: Page,
    card: CardData,
) -> dict[str, Any]:
    """
    Mengambil satu halaman detail lowongan.
    """
    page.goto(
        card.url,
        wait_until="domcontentloaded",
        timeout=PAGE_TIMEOUT_MS,
    )

    page.wait_for_timeout(1_500)

    body_raw = page.locator(
        "body"
    ).inner_text(
        timeout=20_000
    )

    body_text = clean_text(
        body_raw
    ) or ""

    if not body_text:
        raise RuntimeError(
            "Isi halaman detail kosong."
        )

    title = first_matching_text(
        page,
        [
            "main h1",
            "main h2",
            "h1",
            "h2",
        ],
    )

    if (
        not title
        or title.lower()
        in {
            "lowongan magang",
            "maganghub",
        }
    ):
        title = (
            card.title_card
            or title_from_slug(card.url)
        )

    quota = extract_int(
        body_text,
        r"Kuota\s*:?\s*(\d+)",
    )

    applicants = extract_int(
        body_text,
        r"Pelamar\s*:?\s*(\d+)",
    )

    workdays = extract_int(
        body_text,
        r"(\d+)\s*hari"
        r"(?:\s*kerja)?"
        r"\s*(?:per|/)\s*minggu",
    )

    quota = (
        quota
        if quota is not None
        else card.quota_card
    )

    applicants = (
        applicants
        if applicants is not None
        else card.applicants_card
    )

    workdays = (
        workdays
        if workdays is not None
        else card.workdays_card
    )

    description = extract_section(
        body_text,
        "Deskripsi Lowongan",
        [
            "Kualifikasi",
            "Program studi",
            "Tingkat pendidikan",
            "Skill yang Bakal Kamu Dapat",
            "Hari Libur",
            "Lokasi Magang",
            "Durasi Magang",
        ],
    )

    qualification = extract_section(
        body_text,
        "Kualifikasi",
        [
            "Program studi",
            "Tingkat pendidikan",
            "Skill yang Bakal Kamu Dapat",
            "Hari Libur",
            "Lokasi Magang",
            "Durasi Magang",
        ],
    )

    study_program = extract_section(
        body_text,
        "Program studi",
        [
            "Tingkat pendidikan",
            "Hari kerja",
            "Skill yang Bakal Kamu Dapat",
            "Deskripsi Lowongan",
            "Lokasi Magang",
        ],
    )

    education_section = extract_section(
        body_text,
        "Tingkat pendidikan",
        [
            "Program studi",
            "Hari kerja",
            "Skill yang Bakal Kamu Dapat",
            "Deskripsi Lowongan",
            "Lokasi Magang",
        ],
    )

    education = (
        extract_education(
            education_section
        )
        or card.education_card
    )

    skills = extract_section(
        body_text,
        "Skill yang Bakal Kamu Dapat",
        [
            "Hari Libur",
            "Lokasi Magang",
            "Durasi Magang",
            "Kuota",
            "Pelamar",
        ],
    )

    holidays = extract_section(
        body_text,
        "Hari Libur",
        [
            "Lokasi Magang",
            "Durasi Magang",
            "Kuota",
            "Pelamar",
            "Lamar Sekarang",
        ],
    )

    holidays = (
        holidays
        or card.holidays_card
    )

    location_detail = extract_section(
        body_text,
        "Lokasi Magang",
        [
            "Durasi Magang",
            "Kuota",
            "Pelamar",
            "Lamar Sekarang",
        ],
    )

    location = (
        extract_location(body_text)
        or card.location_card
        or CITY_NAME
    )

    organizer = extract_section(
        body_text,
        "Penyelenggara",
        [
            "Deskripsi Lowongan",
            "Kualifikasi",
            "Program studi",
            "Tingkat pendidikan",
            "Lokasi Magang",
        ],
    )

    return {
        "judul_posisi": title,
        "penyelenggara": organizer,
        "lokasi": location,
        "lokasi_detail": location_detail,
        "tingkat_pendidikan": education,
        "program_studi": study_program,
        "hari_kerja_per_minggu": workdays,
        "hari_libur": holidays,
        "kuota": quota,
        "jumlah_pelamar": applicants,
        "deskripsi": description,
        "kualifikasi": qualification,
        "skill_yang_didapat": skills,
        "url": card.url,
        "status_scraping": "Berhasil",
        "jumlah_percobaan": 1,
        "error": None,
    }


def failed_result(
    card: CardData,
    error: Exception,
    attempt_count: int,
) -> dict[str, Any]:
    """
    Menyimpan data cadangan jika detail gagal.
    """
    return {
        "judul_posisi": (
            card.title_card
            or title_from_slug(card.url)
        ),
        "penyelenggara": (
            card.organizer_card
        ),
        "lokasi": (
            card.location_card
            or CITY_NAME
        ),
        "lokasi_detail": None,
        "tingkat_pendidikan": (
            card.education_card
        ),
        "program_studi": (
            card.study_program_card
        ),
        "hari_kerja_per_minggu": (
            card.workdays_card
        ),
        "hari_libur": (
            card.holidays_card
        ),
        "kuota": card.quota_card,
        "jumlah_pelamar": (
            card.applicants_card
        ),
        "deskripsi": None,
        "kualifikasi": None,
        "skill_yang_didapat": None,
        "url": card.url,
        "status_scraping": "Gagal",
        "jumlah_percobaan": attempt_count,
        "error": (
            f"{type(error).__name__}: "
            f"{error}"
        ),
    }


def scrape_detail_with_retry(
    page: Page,
    card: CardData,
) -> dict[str, Any]:
    """
    Mengambil detail dengan maksimal tiga percobaan.
    """
    last_error: Exception | None = None

    for attempt in range(
        1,
        MAX_DETAIL_RETRIES + 1,
    ):
        try:
            result = scrape_detail_once(
                page,
                card,
            )

            result["jumlah_percobaan"] = (
                attempt
            )

            return result

        except Exception as error:
            last_error = error

            print(
                f"  Percobaan {attempt}/"
                f"{MAX_DETAIL_RETRIES} gagal: "
                f"{type(error).__name__}: "
                f"{error}"
            )

            if attempt < MAX_DETAIL_RETRIES:
                time.sleep(
                    attempt * 2
                )

    assert last_error is not None

    return failed_result(
        card,
        last_error,
        MAX_DETAIL_RETRIES,
    )


# ============================================================
# PENGOLAHAN DATA
# ============================================================

def build_dataframe(
    rows: list[dict[str, Any]],
) -> pd.DataFrame:
    """
    Membersihkan dan menghitung analisis persaingan.
    """
    df = pd.DataFrame(rows)

    if df.empty:
        return df

    df = (
        df.drop_duplicates(
            subset=["url"],
            keep="last",
        )
        .reset_index(drop=True)
    )

    numeric_columns = [
        "kuota",
        "jumlah_pelamar",
        "hari_kerja_per_minggu",
        "jumlah_percobaan",
    ]

    for column in numeric_columns:
        if column not in df.columns:
            continue

        df[column] = pd.to_numeric(
            df[column],
            errors="coerce",
        ).astype("Int64")

    quota_numeric = pd.to_numeric(
        df["kuota"],
        errors="coerce",
    )

    applicants_numeric = pd.to_numeric(
        df["jumlah_pelamar"],
        errors="coerce",
    )

    valid_ratio = (
        quota_numeric.gt(0)
        & applicants_numeric.notna()
    )

    df["rasio_pelamar_per_kuota"] = pd.NA

    df.loc[
        valid_ratio,
        "rasio_pelamar_per_kuota",
    ] = (
        applicants_numeric[valid_ratio]
        / quota_numeric[valid_ratio]
    ).round(2)

    df["rasio_pelamar_per_kuota"] = (
        pd.to_numeric(
            df["rasio_pelamar_per_kuota"],
            errors="coerce",
        )
    )

    valid_probability = (
        quota_numeric.gt(0)
        & applicants_numeric.gt(0)
    )

    probability_column = (
        "estimasi_peluang_sederhana_persen"
    )

    df[probability_column] = pd.NA

    df.loc[
        valid_probability,
        probability_column,
    ] = (
        quota_numeric[valid_probability]
        / applicants_numeric[
            valid_probability
        ]
        * 100
    ).clip(
        upper=100
    ).round(2)

    df[probability_column] = (
        pd.to_numeric(
            df[probability_column],
            errors="coerce",
        )
    )

    df["kategori_persaingan"] = (
        df["rasio_pelamar_per_kuota"]
        .apply(competition_category)
    )

    df["kota_filter"] = CITY_NAME
    df["city_id_filter"] = CITY_ID

    df["tanggal_scraping"] = (
        datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )

    df = df.sort_values(
        by=[
            "rasio_pelamar_per_kuota",
            "jumlah_pelamar",
            "judul_posisi",
        ],
        ascending=[
            True,
            True,
            True,
        ],
        na_position="last",
    ).reset_index(drop=True)

    df.insert(
        0,
        "peringkat_peluang",
        range(1, len(df) + 1),
    )

    return df


# ============================================================
# PENYIMPANAN
# ============================================================

def get_output_paths() -> tuple[Path, Path]:
    """
    Membuat nama file final dan checkpoint.
    """
    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    city_slug = slugify_filename(
        CITY_NAME
    )

    final_path = OUTPUT_DIR / (
        f"lowongan_maganghub_"
        f"{city_slug}_"
        f"{timestamp}.csv"
    )

    checkpoint_path = OUTPUT_DIR / (
        f"checkpoint_maganghub_"
        f"{city_slug}_"
        f"{timestamp}.csv"
    )

    return final_path, checkpoint_path


def save_dataframe(
    df: pd.DataFrame,
    output_path: Path,
) -> None:
    """
    Menyimpan DataFrame ke CSV.
    """
    df.to_csv(
        output_path,
        index=False,
        encoding="utf-8-sig",
    )


def save_checkpoint(
    rows: list[dict[str, Any]],
    checkpoint_path: Path,
) -> None:
    """
    Menyimpan hasil sementara.
    """
    if not rows:
        return

    checkpoint_df = build_dataframe(
        rows
    )

    save_dataframe(
        checkpoint_df,
        checkpoint_path,
    )


# ============================================================
# BROWSER
# ============================================================

def create_browser_context(
    browser: Browser,
) -> BrowserContext:
    """
    Membuat browser context Playwright.
    """
    return browser.new_context(
        user_agent=(
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/150.0.0.0 "
            "Safari/537.36"
        ),
        locale="id-ID",
        viewport={
            "width": 1440,
            "height": 1000,
        },
        ignore_https_errors=False,
    )


# ============================================================
# PROSES UTAMA
# ============================================================

def run_scraper(
    checkpoint_path: Path,
) -> tuple[pd.DataFrame, int | None]:
    """
    Menjalankan scraping daftar dan detail.
    """
    rows: list[dict[str, Any]] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=HEADLESS,
            args=[
                "--disable-dev-shm-usage",
                (
                    "--disable-blink-features="
                    "AutomationControlled"
                ),
            ],
        )

        context = create_browser_context(
            browser
        )

        list_page = context.new_page()

        list_page.set_default_timeout(
            DEFAULT_TIMEOUT_MS
        )

        try:
            cards, total_expected = (
                collect_listing_cards(
                    list_page
                )
            )
        finally:
            list_page.close()

        if not cards:
            context.close()
            browser.close()

            raise RuntimeError(
                "Tidak ada URL lowongan ditemukan."
            )

        detail_page = context.new_page()

        detail_page.set_default_timeout(
            DEFAULT_TIMEOUT_MS
        )

        total_cards = len(cards)

        for index, card in enumerate(
            cards,
            start=1,
        ):
            print(
                f"[{index:03d}/{total_cards:03d}] "
                f"{card.url}"
            )

            result = scrape_detail_with_retry(
                detail_page,
                card,
            )

            rows.append(result)

            print(
                f"  Status akhir: "
                f"{result['status_scraping']}"
            )

            if (
                index % CHECKPOINT_INTERVAL == 0
                or index == total_cards
            ):
                save_checkpoint(
                    rows,
                    checkpoint_path,
                )

                print(
                    f"  Checkpoint: "
                    f"{len(rows)} data"
                )

            time.sleep(
                REQUEST_DELAY_SECONDS
            )

        detail_page.close()
        context.close()
        browser.close()

    return (
        build_dataframe(rows),
        total_expected,
    )


# ============================================================
# VERIFIKASI DAN RINGKASAN
# ============================================================

def verify_final_data(
    df: pd.DataFrame,
    total_expected: int | None,
) -> None:
    """
    Memastikan jumlah URL final sesuai total website.
    """
    unique_count = int(
        df["url"].nunique()
    )

    if (
        total_expected is not None
        and unique_count != total_expected
    ):
        raise RuntimeError(
            "Verifikasi final gagal. "
            f"Total website: {total_expected}, "
            f"URL unik hasil: {unique_count}."
        )


def print_summary(
    df: pd.DataFrame,
    final_path: Path,
    checkpoint_path: Path,
    total_expected: int | None,
) -> None:
    """
    Menampilkan ringkasan.
    """
    success_count = int(
        (
            df["status_scraping"]
            == "Berhasil"
        ).sum()
    )

    failed_count = int(
        (
            df["status_scraping"]
            == "Gagal"
        ).sum()
    )

    unique_url_count = int(
        df["url"].nunique()
    )

    print()
    print("=" * 75)
    print("SCRAPING SELESAI")
    print("=" * 75)
    print(f"Filter kota       : {CITY_NAME}")
    print(f"Total website     : {total_expected}")
    print(f"Jumlah data final : {len(df)}")
    print(f"URL unik          : {unique_url_count}")
    print(f"Berhasil          : {success_count}")
    print(f"Gagal             : {failed_count}")
    print(f"File final        : {final_path}")
    print(f"File checkpoint   : {checkpoint_path}")
    print("=" * 75)

    preview_columns = [
        "peringkat_peluang",
        "judul_posisi",
        "lokasi",
        "kuota",
        "jumlah_pelamar",
        "rasio_pelamar_per_kuota",
        "kategori_persaingan",
        "status_scraping",
    ]

    print()
    print(
        "10 LOWONGAN DENGAN "
        "RASIO PERSAINGAN TERENDAH"
    )
    print("-" * 75)

    print(
        df[preview_columns]
        .head(10)
        .to_string(index=False)
    )


# ============================================================
# PROGRAM UTAMA
# ============================================================

def main() -> int:
    final_path, checkpoint_path = (
        get_output_paths()
    )

    try:
        validate_configuration()

        print("=" * 75)
        print("SCRAPER MAGANGHUB KEMNAKER")
        print("=" * 75)
        print(f"Kota          : {CITY_NAME}")
        print(
            f"Jenjang       : "
            f"{EDUCATION_LEVEL_LABEL}"
        )

        print(
            "Program studi : "
            + ", ".join(
                program["label"]
                for program in STUDY_PROGRAMS
            )
        )

        print(
            "Urutan        : "
            "Pelamar terbanyak"
        )

        print(
            f"Wajib lengkap : "
            f"{REQUIRE_COMPLETE_LIST}"
        )

        print("=" * 75)
        print()

        df, total_expected = run_scraper(
            checkpoint_path
        )

        if df.empty:
            raise RuntimeError(
                "Tidak ada data yang diproses."
            )

        verify_final_data(
            df,
            total_expected,
        )

        save_dataframe(
            df,
            final_path,
        )

        print_summary(
            df=df,
            final_path=final_path,
            checkpoint_path=checkpoint_path,
            total_expected=total_expected,
        )

        return 0

    except KeyboardInterrupt:
        print(
            "\nProgram dihentikan pengguna."
        )

        print(
            "Periksa checkpoint:"
        )

        print(checkpoint_path)
        return 130

    except ModuleNotFoundError as error:
        print(
            "\nLibrary belum terpasang:"
        )

        print(error)

        print("\nJalankan:")
        print(
            "python -m pip install "
            "playwright pandas"
        )

        print(
            "python -m playwright "
            "install chromium"
        )

        return 1

    except Exception as error:
        print(
            "\nProgram mengalami kesalahan:"
        )

        print(
            f"{type(error).__name__}: "
            f"{error}"
        )

        if checkpoint_path.exists():
            print(
                "Checkpoint tersedia:"
            )

            print(checkpoint_path)

        return 1


if __name__ == "__main__":
    sys.exit(main())
