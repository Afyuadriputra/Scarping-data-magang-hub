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
# False = browser terlihat saat scraping
HEADLESS = True

REQUEST_DELAY_SECONDS = 1.5
PAGE_TIMEOUT_MS = 120_000
DEFAULT_TIMEOUT_MS = 30_000
MAX_LOAD_MORE_ATTEMPTS = 30
MAX_NO_CHANGE_ATTEMPTS = 4


# ============================================================
# KONFIGURASI FILTER
# ============================================================

CITY_NAME = "Kota Batam"

# WAJIB DIGANTI.
#
# Cara mendapatkan ID:
# 1. Buka MagangHub.
# 2. Pilih filter Kota Batam.
# 3. Terapkan filter.
# 4. Salin nilai city_id[0][id] dari URL.
#
# Contoh bagian URL:
# city_id%5B0%5D%5Bid%5D=UUID_KOTA_Batam
CITY_ID = "c14e61c2-18bb-4f46-b51f-5bd78ff33705"

EDUCATION_LEVEL_ID = "bachelor"
EDUCATION_LEVEL_LABEL = "Sarjana"

# False agar sama dengan URL dari browser:
# tidak membatasi lowongan hanya untuk jenjang Sarjana.
FILTER_EDUCATION_LEVEL = False

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
        "city_id[0][id]": CITY_ID,
        "city_id[0][label]": CITY_NAME,
        "sort": "most_applicants",
    }

    if FILTER_EDUCATION_LEVEL:
        params["education_level[0][id]"] = (
            EDUCATION_LEVEL_ID
        )
        params["education_level[0][label]"] = (
            EDUCATION_LEVEL_LABEL
        )

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
# MODEL DATA KARTU LOWONGAN
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
# VALIDASI KONFIGURASI
# ============================================================

def validate_configuration() -> None:
    """
    Memastikan konfigurasi sudah diisi dengan benar.
    """
    invalid_city_ids = {
        "",
        "MASUKKAN_ID_KOTA_Batam",
        "MASUKKAN_ID_KOTA_Batam_DI_SINI",
    }

    if CITY_ID.strip() in invalid_city_ids:
        raise ValueError(
            "CITY_ID Kota Batam belum diisi.\n\n"
            "Buka MagangHub, pilih filter Kota Batam, lalu salin "
            "nilai city_id[0][id] dari URL ke variabel CITY_ID."
        )

    if not STUDY_PROGRAMS:
        raise ValueError(
            "Daftar program studi tidak boleh kosong."
        )


# ============================================================
# FUNGSI UTILITAS
# ============================================================

def clean_text(value: Any) -> str | None:
    """
    Membersihkan spasi, tab, dan baris kosong berlebihan.
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
    Mengambil angka pertama berdasarkan pola regex.
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
    Mengambil teks di antara judul awal dan judul berikutnya.
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

        if (
            position != -1
            and position < content_end
        ):
            content_end = position

    return clean_text(
        remaining[:content_end]
    )


def first_matching_text(
    page: Page,
    selectors: list[str],
) -> str | None:
    """
    Mengambil teks pertama yang tidak kosong dari selector.
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
    Mengambil nama kota atau kabupaten dari teks.
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
        r"Tingkat pendidikan|$"
        r"))",
        text,
        flags=re.IGNORECASE,
    )

    if not match:
        return None

    return clean_text(
        match.group(0)
    )


def extract_education(
    text: str | None,
) -> str | None:
    """
    Mengambil daftar jenjang pendidikan dari teks.
    """
    if not text:
        return None

    found = [
        level
        for level in (
            "Diploma",
            "Sarjana",
            "Profesi",
        )
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
    Membuat kategori persaingan berdasarkan rasio pelamar.
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
    Membuat judul cadangan dari slug URL.
    """
    slug = url.rstrip("/").split("/")[-1]

    slug = re.sub(
        (
            r"-[0-9a-f]{8}"
            r"-[0-9a-f]{4}"
            r"-[0-9a-f]{4}"
            r"-[0-9a-f]{4}"
            r"-[0-9a-f]{12}$"
        ),
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

    Contoh:
    Kota Batam -> kota_Batam
    """
    value = value.lower().strip()

    value = re.sub(
        r"[^a-z0-9]+",
        "_",
        value,
    )

    return value.strip("_")


# ============================================================
# PARSING KARTU LOWONGAN
# ============================================================

def parse_card_text(
    url: str,
    card_text: str | None,
) -> CardData:
    """
    Mengambil data dasar dari kartu lowongan.
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
        (
            r"Hari Libur\s+(.+?)"
            r"(?=\s+(?:Kuota|Pelamar|$))"
        ),
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
# PEMBACAAN JUMLAH LOWONGAN
# ============================================================

def get_total_listings(
    page_text: str,
) -> int | None:
    """
    Membaca teks:
    'Ditemukan 24 lowongan'
    """
    return extract_int(
        page_text,
        r"Ditemukan\s+(\d+)\s+lowongan",
    )


def get_display_progress(
    page_text: str,
) -> tuple[int | None, int | None]:
    """
    Membaca teks:
    'Menampilkan 18 dari 24 lowongan'
    """
    match = re.search(
        (
            r"Menampilkan\s+(\d+)"
            r"\s+dari\s+(\d+)"
            r"\s+lowongan"
        ),
        page_text,
        flags=re.IGNORECASE,
    )

    if not match:
        return None, None

    return (
        int(match.group(1)),
        int(match.group(2)),
    )


def get_detail_urls(
    page: Page,
) -> set[str]:
    """
    Mengambil seluruh URL detail lowongan yang sedang tampil.
    """
    hrefs = page.locator(
        'a[href*="/magang-nasional/lowongan/"]'
    ).evaluate_all(
        """
        elements => elements.map(
            element => element.href
        )
        """
    )

    urls: set[str] = set()

    for href in hrefs:
        if not href:
            continue

        normalized_url = (
            href
            .split("#")[0]
            .split("?")[0]
            .rstrip("/")
        )

        if re.search(
            (
                r"/magang-nasional/"
                r"lowongan/[^/?#]+$"
            ),
            normalized_url,
            flags=re.IGNORECASE,
        ):
            urls.add(normalized_url)

    return urls


def count_unique_detail_urls(
    page: Page,
) -> int:
    """
    Menghitung jumlah URL detail unik.
    """
    return len(
        get_detail_urls(page)
    )


# ============================================================
# PAGINATION / LOAD MORE
# ============================================================

def scroll_to_bottom(
    page: Page,
) -> None:
    """
    Scroll halaman dan semua container ke posisi paling bawah.
    """
    page.keyboard.press("End")
    page.wait_for_timeout(750)

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

            const scrollableElements = elements.filter(
                element => {
                    const style = getComputedStyle(element);

                    const scrollable = [
                        "auto",
                        "scroll"
                    ].includes(style.overflowY);

                    return (
                        scrollable
                        && element.scrollHeight
                            > element.clientHeight
                    );
                }
            );

            for (
                const element
                of scrollableElements
            ) {
                element.scrollTop =
                    element.scrollHeight;
            }
        }
        """
    )

    page.wait_for_timeout(1_500)


def click_load_more(
    page: Page,
) -> bool:
    """
    Mencari dan menekan tombol halaman berikutnya.
    """
    text_pattern = re.compile(
        (
            r"Muat\s+Lebih\s+Banyak|"
            r"Lihat\s+Lebih\s+Banyak|"
            r"Tampilkan\s+Lebih\s+Banyak|"
            r"Load\s+More|"
            r"Berikutnya|"
            r"Selanjutnya|"
            r"Next"
        ),
        flags=re.IGNORECASE,
    )

    candidate_locators = [
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
    ]

    for locator in candidate_locators:
        try:
            count = locator.count()
        except Exception:
            continue

        for index in range(count):
            candidate = locator.nth(index)

            try:
                if not candidate.is_visible():
                    continue

                if not candidate.is_enabled():
                    continue

                candidate.scroll_into_view_if_needed()
                page.wait_for_timeout(300)

                try:
                    candidate.click(
                        timeout=5_000
                    )
                except Exception:
                    candidate.evaluate(
                        "(element) => element.click()"
                    )

                return True

            except Exception:
                continue

    icon_locator = page.locator(
        (
            'button[aria-label*="next" i], '
            'a[aria-label*="next" i], '
            'button[title*="next" i], '
            'a[title*="next" i], '
            'button[aria-label*="berikut" i], '
            'a[aria-label*="berikut" i]'
        )
    )

    for index in reversed(
        range(icon_locator.count())
    ):
        candidate = icon_locator.nth(index)

        try:
            if not candidate.is_visible():
                continue

            disabled = candidate.get_attribute(
                "disabled"
            )

            aria_disabled = candidate.get_attribute(
                "aria-disabled"
            )

            if (
                disabled is not None
                or aria_disabled == "true"
            ):
                continue

            candidate.scroll_into_view_if_needed()

            candidate.click(
                timeout=5_000
            )

            return True

        except Exception:
            continue

    return False


def wait_for_new_listings(
    page: Page,
    previous_count: int,
) -> bool:
    """
    Menunggu sampai jumlah URL lowongan bertambah.
    """
    try:
        page.wait_for_function(
            r"""
            previousCount => {
                const links = [
                    ...document.querySelectorAll(
                        'a[href*="/magang-nasional/lowongan/"]'
                    )
                ];

                const urls = new Set(
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
                );

                return urls.size > previousCount;
            }
            """,
            arg=previous_count,
            timeout=15_000,
        )

        return True

    except PlaywrightTimeoutError:
        return False


# ============================================================
# SCRAPING SELURUH HALAMAN DAFTAR
# ============================================================

def collect_listing_cards(
    page: Page,
) -> list[CardData]:
    """
    Membuka seluruh halaman, memuat seluruh kartu,
    dan mengumpulkan semua URL detail.
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

    if total_expected is not None:
        print(
            "Jumlah lowongan menurut website: "
            f"{total_expected}"
        )
    else:
        print(
            "Jumlah total lowongan "
            "tidak dapat dibaca."
        )

    if displayed is not None:
        print(
            "Data pada halaman awal: "
            f"{displayed}"
        )

    previous_count = (
        count_unique_detail_urls(page)
    )

    no_change_attempts = 0

    for attempt in range(
        1,
        MAX_LOAD_MORE_ATTEMPTS + 1,
    ):
        current_count = (
            count_unique_detail_urls(page)
        )

        progress_text = (
            f"{current_count}/{total_expected}"
            if total_expected is not None
            else str(current_count)
        )

        print(
            f"Memuat data: {progress_text}"
        )

        if (
            total_expected is not None
            and current_count >= total_expected
        ):
            break

        scroll_to_bottom(page)

        clicked = click_load_more(page)

        if clicked:
            print(
                "  Tombol berikutnya ditekan."
            )

            changed = wait_for_new_listings(
                page,
                current_count,
            )

            if not changed:
                page.wait_for_timeout(2_000)

        else:
            page.wait_for_timeout(2_500)

        new_count = (
            count_unique_detail_urls(page)
        )

        if new_count > previous_count:
            previous_count = new_count
            no_change_attempts = 0

        else:
            no_change_attempts += 1

        if (
            no_change_attempts
            >= MAX_NO_CHANGE_ATTEMPTS
        ):
            print(
                "Tidak ada tambahan data setelah "
                f"{MAX_NO_CHANGE_ATTEMPTS} percobaan."
            )
            break

    links = page.locator(
        'a[href*="/magang-nasional/lowongan/"]'
    )

    cards: list[CardData] = []
    visited_urls: set[str] = set()

    for index in range(links.count()):
        link = links.nth(index)

        href = link.get_attribute("href")

        if not href:
            continue

        absolute_url = urljoin(
            BASE_URL,
            href,
        )

        absolute_url = (
            absolute_url
            .split("#")[0]
            .split("?")[0]
            .rstrip("/")
        )

        if not re.search(
            (
                r"/magang-nasional/"
                r"lowongan/[^/?#]+$"
            ),
            absolute_url,
            flags=re.IGNORECASE,
        ):
            continue

        if absolute_url in visited_urls:
            continue

        try:
            card_text = link.inner_text(
                timeout=3_000
            )

        except Exception:
            try:
                card_text = (
                    link.locator("xpath=..")
                    .inner_text(timeout=3_000)
                )
            except Exception:
                card_text = None

        cards.append(
            parse_card_text(
                absolute_url,
                card_text,
            )
        )

        visited_urls.add(
            absolute_url
        )

    print()
    print(
        "URL detail unik ditemukan: "
        f"{len(cards)}"
    )

    if (
        total_expected is not None
        and len(cards) < total_expected
    ):
        print(
            "PERINGATAN: website menyatakan "
            f"ada {total_expected} lowongan, "
            f"tetapi script menemukan "
            f"{len(cards)} URL."
        )

    elif total_expected is not None:
        print(
            f"Semua {total_expected} lowongan "
            "berhasil ditemukan."
        )

    print()

    return cards


# ============================================================
# SCRAPING HALAMAN DETAIL
# ============================================================

def scrape_detail(
    page: Page,
    card: CardData,
) -> dict[str, Any]:
    """
    Mengambil informasi dari halaman detail lowongan.
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
        (
            r"(\d+)\s*hari"
            r"(?:\s*kerja)?"
            r"\s*(?:per|/)\s*minggu"
        ),
    )

    if quota is None:
        quota = card.quota_card

    if applicants is None:
        applicants = (
            card.applicants_card
        )

    if workdays is None:
        workdays = card.workdays_card

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

    if holidays is None:
        holidays = card.holidays_card

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
        "error": None,
    }


def failed_result(
    card: CardData,
    error: Exception,
) -> dict[str, Any]:
    """
    Membuat data cadangan ketika detail gagal diambil.
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
        "error": (
            f"{type(error).__name__}: "
            f"{error}"
        ),
    }


# ============================================================
# PENGOLAHAN DATA
# ============================================================

def build_dataframe(
    rows: list[dict[str, Any]],
) -> pd.DataFrame:
    """
    Membersihkan dan mengolah hasil scraping.
    """
    df = pd.DataFrame(rows)

    if df.empty:
        return df

    df = (
        df.drop_duplicates(
            subset=["url"]
        )
        .reset_index(drop=True)
    )

    numeric_columns = [
        "kuota",
        "jumlah_pelamar",
        "hari_kerja_per_minggu",
    ]

    for column in numeric_columns:
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

    df[
        "estimasi_peluang_sederhana_persen"
    ] = pd.NA

    df.loc[
        valid_probability,
        "estimasi_peluang_sederhana_persen",
    ] = (
        quota_numeric[valid_probability]
        / applicants_numeric[
            valid_probability
        ]
        * 100
    ).clip(
        upper=100
    ).round(2)

    df[
        "estimasi_peluang_sederhana_persen"
    ] = pd.to_numeric(
        df[
            "estimasi_peluang_sederhana_persen"
        ],
        errors="coerce",
    )

    df["kategori_persaingan"] = (
        df["rasio_pelamar_per_kuota"]
        .apply(competition_category)
    )

    df["kota_filter"] = CITY_NAME

    df["tanggal_scraping"] = (
        datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    )

    df = df.sort_values(
        by=[
            "rasio_pelamar_per_kuota",
            "jumlah_pelamar",
        ],
        ascending=[
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
# PENYIMPANAN SATU FILE CSV
# ============================================================

def save_output_csv(
    df: pd.DataFrame,
) -> Path:
    """
    Menyimpan seluruh hasil dari semua halaman ke satu CSV.
    """
    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    city_slug = slugify_filename(
        CITY_NAME
    )

    csv_path = OUTPUT_DIR / (
        f"lowongan_maganghub_"
        f"{city_slug}_"
        f"{timestamp}.csv"
    )

    output_df = (
        df.drop_duplicates(
            subset=["url"]
        )
        .reset_index(drop=True)
    )

    output_df.to_csv(
        csv_path,
        index=False,
        encoding="utf-8-sig",
    )

    return csv_path


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
# PROSES SCRAPING UTAMA
# ============================================================

def run_scraper() -> pd.DataFrame:
    """
    Menjalankan scraping halaman daftar dan detail.
    """
    rows: list[dict[str, Any]] = []

    with sync_playwright() as playwright:
        browser = (
            playwright.chromium.launch(
                headless=HEADLESS,
                args=[
                    "--disable-dev-shm-usage",
                    (
                        "--disable-blink-features="
                        "AutomationControlled"
                    ),
                ],
            )
        )

        context = create_browser_context(
            browser
        )

        list_page = context.new_page()

        list_page.set_default_timeout(
            DEFAULT_TIMEOUT_MS
        )

        try:
            cards = collect_listing_cards(
                list_page
            )
        finally:
            list_page.close()

        if not cards:
            context.close()
            browser.close()

            raise RuntimeError(
                "Tidak ada URL lowongan yang ditemukan. "
                "Periksa CITY_ID, filter, koneksi internet, "
                "atau struktur halaman website."
            )

        detail_page = context.new_page()

        detail_page.set_default_timeout(
            DEFAULT_TIMEOUT_MS
        )

        total = len(cards)

        for index, card in enumerate(
            cards,
            start=1,
        ):
            print(
                f"[{index:03d}/{total:03d}] "
                f"{card.url}"
            )

            try:
                result = scrape_detail(
                    detail_page,
                    card,
                )

                rows.append(result)

                print(
                    "  Status: berhasil"
                )

            except PlaywrightTimeoutError as error:
                rows.append(
                    failed_result(
                        card,
                        error,
                    )
                )

                print(
                    "  Status: gagal karena timeout"
                )

            except Exception as error:
                rows.append(
                    failed_result(
                        card,
                        error,
                    )
                )

                print(
                    "  Status: gagal — "
                    f"{type(error).__name__}: "
                    f"{error}"
                )

            time.sleep(
                REQUEST_DELAY_SECONDS
            )

        detail_page.close()
        context.close()
        browser.close()

    return build_dataframe(rows)


# ============================================================
# RINGKASAN HASIL
# ============================================================

def print_summary(
    df: pd.DataFrame,
    csv_path: Path,
) -> None:
    """
    Menampilkan ringkasan hasil scraping.
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
    print(
        f"Filter kota : {CITY_NAME}"
    )
    print(
        f"Jumlah data : {len(df)}"
    )
    print(
        f"URL unik    : {unique_url_count}"
    )
    print(
        f"Berhasil    : {success_count}"
    )
    print(
        f"Gagal       : {failed_count}"
    )
    print(
        f"File CSV    : {csv_path}"
    )
    print("=" * 75)

    preview_columns = [
        "peringkat_peluang",
        "judul_posisi",
        "lokasi",
        "kuota",
        "jumlah_pelamar",
        "rasio_pelamar_per_kuota",
        "kategori_persaingan",
    ]

    existing_columns = [
        column
        for column in preview_columns
        if column in df.columns
    ]

    print()
    print(
        "10 LOWONGAN DENGAN "
        "RASIO PERSAINGAN TERENDAH"
    )
    print("-" * 75)

    print(
        df[existing_columns]
        .head(10)
        .to_string(index=False)
    )


# ============================================================
# PROGRAM UTAMA
# ============================================================

def main() -> int:
    try:
        validate_configuration()

        print("=" * 75)
        print("SCRAPER MAGANGHUB KEMNAKER")
        print("=" * 75)
        print(
            f"Kota          : {CITY_NAME}"
        )
        print(
            f"Jenjang       : "
            + (
                EDUCATION_LEVEL_LABEL
                if FILTER_EDUCATION_LEVEL
                else "Semua jenjang"
            )
        )

        program_labels = [
            program["label"]
            for program in STUDY_PROGRAMS
        ]

        print(
            "Program studi : "
            + ", ".join(program_labels)
        )

        print(
            "Urutan        : "
            "Pelamar terbanyak"
        )

        print("=" * 75)
        print()

        df = run_scraper()

        if df.empty:
            print(
                "Tidak ada data yang "
                "berhasil diproses."
            )
            return 1

        csv_path = save_output_csv(df)

        print_summary(
            df=df,
            csv_path=csv_path,
        )

        return 0

    except KeyboardInterrupt:
        print(
            "\nProgram dihentikan "
            "oleh pengguna."
        )
        return 130

    except ModuleNotFoundError as error:
        print(
            "\nLibrary belum terpasang:"
        )
        print(error)

        print(
            "\nJalankan perintah berikut:"
        )
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

        return 1


if __name__ == "__main__":
    sys.exit(main())
