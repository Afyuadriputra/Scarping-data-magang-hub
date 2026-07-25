from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import (
    Page,
    Request,
    Response,
    sync_playwright,
)


TARGET_URL = (
    "https://maganghub.kemnaker.go.id/"
    "magang-nasional/lowongan"
)

OUTPUT_DIR = (
    Path(__file__).resolve().parent
    / "hasil_diagnostik"
)

OUTPUT_DIR.mkdir(
    parents=True,
    exist_ok=True,
)


def on_request(request: Request) -> None:
    """
    Menampilkan request Fetch/XHR yang dibuat halaman.
    """
    if request.resource_type not in {
        "fetch",
        "xhr",
    }:
        return

    print()
    print("[REQUEST API]")
    print(f"Method : {request.method}")
    print(f"URL    : {request.url}")

    if request.post_data:
        print(f"Payload: {request.post_data}")


def on_response(response: Response) -> None:
    """
    Menampilkan response Fetch/XHR yang berkaitan
    dengan lowongan, pagination, atau pencarian.
    """
    request = response.request

    if request.resource_type not in {
        "fetch",
        "xhr",
    }:
        return

    url_lower = response.url.lower()

    important_keywords = [
        "lowongan",
        "vacancy",
        "internship",
        "magang",
        "search",
        "page",
        "pagination",
    ]

    if not any(
        keyword in url_lower
        for keyword in important_keywords
    ):
        return

    print()
    print("[RESPONSE API]")
    print(f"Status : {response.status}")
    print(f"URL    : {response.url}")


def print_element_information(
    page: Page,
) -> None:
    """
    Menampilkan seluruh tombol, link, dan elemen
    dengan role button yang terlihat.
    """
    elements = page.locator(
        "button, a, [role='button']"
    )

    print()
    print("=" * 100)
    print("DAFTAR TOMBOL, LINK, DAN ROLE BUTTON")
    print("=" * 100)

    visible_number = 0

    for index in range(elements.count()):
        element = elements.nth(index)

        try:
            if not element.is_visible():
                continue

            data = element.evaluate(
                """
                element => {
                    const rect =
                        element.getBoundingClientRect();

                    return {
                        tag: element.tagName,
                        text: (
                            element.innerText || ""
                        )
                            .replace(/\\s+/g, " ")
                            .trim(),
                        ariaLabel:
                            element.getAttribute(
                                "aria-label"
                            ),
                        title:
                            element.getAttribute(
                                "title"
                            ),
                        href:
                            element.getAttribute(
                                "href"
                            ),
                        role:
                            element.getAttribute(
                                "role"
                            ),
                        type:
                            element.getAttribute(
                                "type"
                            ),
                        disabled:
                            Boolean(
                                element.disabled
                            ),
                        ariaDisabled:
                            element.getAttribute(
                                "aria-disabled"
                            ),
                        testId:
                            element.getAttribute(
                                "data-testid"
                            ),
                        className:
                            String(
                                element.className
                                || ""
                            ),
                        id:
                            element.id || null,
                        x: Math.round(rect.x),
                        y: Math.round(rect.y),
                        width:
                            Math.round(rect.width),
                        height:
                            Math.round(rect.height)
                    };
                }
                """
            )

            visible_number += 1

            print()
            print(
                f"[{visible_number}] "
                f"DOM index: {index}"
            )

            print(
                json.dumps(
                    data,
                    ensure_ascii=False,
                    indent=2,
                )
            )

        except Exception as error:
            print(
                f"Gagal membaca elemen "
                f"indeks {index}: {error}"
            )

    print()
    print(
        f"Total elemen terlihat: "
        f"{visible_number}"
    )


def save_page_information(
    page: Page,
    suffix: str,
) -> None:
    """
    Menyimpan screenshot, HTML, dan teks halaman.
    """
    screenshot_path = (
        OUTPUT_DIR
        / f"halaman_{suffix}.png"
    )

    html_path = (
        OUTPUT_DIR
        / f"halaman_{suffix}.html"
    )

    text_path = (
        OUTPUT_DIR
        / f"halaman_{suffix}.txt"
    )

    page.screenshot(
        path=str(screenshot_path),
        full_page=True,
    )

    html_path.write_text(
        page.content(),
        encoding="utf-8",
    )

    body_text = page.locator(
        "body"
    ).inner_text()

    text_path.write_text(
        body_text,
        encoding="utf-8",
    )

    print()
    print("File diagnostik tersimpan:")
    print(screenshot_path)
    print(html_path)
    print(text_path)


def get_lowongan_urls(
    page: Page,
) -> list[str]:
    """
    Mengambil URL detail lowongan yang sedang tampil.
    """
    urls = page.locator(
        'a[href*="/magang-nasional/lowongan/"]'
    ).evaluate_all(
        """
        elements => [
            ...new Set(
                elements
                    .map(element =>
                        element.href
                            .split("#")[0]
                            .split("?")[0]
                            .replace(/\\/$/, "")
                    )
                    .filter(url =>
                        /\\/magang-nasional\\/lowongan\\/[^/?#]+$/
                            .test(url)
                    )
            )
        ]
        """
    )

    return sorted(urls)


def print_lowongan_count(
    page: Page,
    label: str,
) -> list[str]:
    """
    Menampilkan jumlah URL lowongan.
    """
    urls = get_lowongan_urls(page)

    print()
    print(
        f"{label}: "
        f"{len(urls)} URL detail"
    )

    for number, url in enumerate(
        urls,
        start=1,
    ):
        print(
            f"  {number:02d}. {url}"
        )

    return urls


def scroll_step_by_step(
    page: Page,
) -> None:
    """
    Scroll bertahap untuk melihat apakah halaman
    menggunakan infinite scroll.
    """
    print()
    print("=" * 100)
    print("UJI INFINITE SCROLL")
    print("=" * 100)

    previous_urls = set(
        get_lowongan_urls(page)
    )

    for step in range(1, 11):
        page.evaluate(
            """
            () => {
                window.scrollBy({
                    top:
                        window.innerHeight * 0.8,
                    behavior: "instant"
                });
            }
            """
        )

        page.wait_for_timeout(2_000)

        current_urls = set(
            get_lowongan_urls(page)
        )

        print(
            f"Scroll {step}: "
            f"{len(current_urls)} URL"
        )

        new_urls = (
            current_urls
            - previous_urls
        )

        if new_urls:
            print(
                f"  Ada {len(new_urls)} "
                "URL baru:"
            )

            for url in sorted(new_urls):
                print(f"  + {url}")

        previous_urls = current_urls


def main() -> None:
    with sync_playwright() as playwright:
        browser = (
            playwright.chromium.launch(
                headless=False,
            )
        )

        context = browser.new_context(
            locale="id-ID",
            viewport={
                "width": 1440,
                "height": 1000,
            },
        )

        page = context.new_page()

        page.on(
            "request",
            on_request,
        )

        page.on(
            "response",
            on_response,
        )

        print("Membuka halaman:")
        print(TARGET_URL)

        page.goto(
            TARGET_URL,
            wait_until="domcontentloaded",
            timeout=120_000,
        )

        page.wait_for_timeout(5_000)

        print_lowongan_count(
            page,
            "Sebelum scroll",
        )

        print_element_information(
            page
        )

        save_page_information(
            page,
            "sebelum_scroll",
        )

        scroll_step_by_step(
            page
        )

        page.evaluate(
            """
            () => window.scrollTo({
                top:
                    document.documentElement
                        .scrollHeight,
                behavior: "instant"
            })
            """
        )

        page.wait_for_timeout(3_000)

        print_lowongan_count(
            page,
            "Setelah scroll paling bawah",
        )

        print_element_information(
            page
        )

        save_page_information(
            page,
            "setelah_scroll",
        )

        print()
        input(
            "Browser dibiarkan terbuka. "
            "Tekan Enter untuk menutup..."
        )

        context.close()
        browser.close()


if __name__ == "__main__":
    main()