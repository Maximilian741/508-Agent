"""Smoke: the catalog of what the offline (no-AI) rules may and may not write.

``app.ai.offline_rules`` decides what the heuristic provider writes into a
customer's file when there is no AI key, and vets whatever an AI provider
answers. This pins the decision for every case the heuristic-quality scout
catalogued (and the good cases that must keep working), one line each, so a
future "just make it fix more" change has to argue with a named example.

  * language: 5 realistic samples per language (notices, letters, reports) in
    en/es/fr/de/it/pt/nl must come back right — never another language — and a
    bilingual page, a snippet, lorem ipsum, Swedish or Tagalog must ABSTAIN;
  * link text: only words in the address; anchors, email/phone, home pages
    and code-like slugs are refused; "Read more about <old text>" never passes;
  * alt text: only a caption written for the picture; nav bars, bylines,
    credits, file names, URLs and "click to enlarge" never become alt;
  * titles: never a camera/scanner/chat export name or a heading like "1";
  * table captions: never a placeholder or a re-list of the column names;
  * header rows: never a row of numbers, a label/value form or a data row.

Run: python -m app.devtools.smoke_semantic_rules_catalog
"""

from __future__ import annotations

import os
import sys

for _key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "SEMANTIC_PROVIDER"):
    os.environ.pop(_key, None)

from app.ai.offline_rules import (  # noqa: E402
    alt_from_caption,
    detect_latin_language,
    link_text_from_target,
    title_from_filename,
    title_from_heading,
    vet_alt_text,
    vet_link_text,
    vet_table_caption,
)
from app.ai.semantic_inference import HeuristicProvider  # noqa: E402
from app.analyzers.image_analyzer import is_nondescriptive_alt  # noqa: E402

LANGUAGE_SAMPLES = {
    "en": [
        "The quarterly report is attached for your review. Please send comments to the finance team by Friday.",
        "Open enrollment for health benefits begins on November 1. Employees who do not make a selection will keep their current plan. Contact Human Resources with any questions about coverage or eligibility.",
        "This notice explains your rights under the Family and Medical Leave Act. You may be eligible for up to twelve weeks of unpaid, job-protected leave each year.",
        "Residents should place recycling bins at the curb by 7 a.m. on the scheduled collection day. Bins that are overfilled or contain food waste will not be collected.",
        "The Château de Versailles and the Musée de la Mode are in the Île-de-France region of France, near the Seine.",
    ],
    "es": [
        "El informe trimestral se adjunta para su revisión. Por favor envíe sus comentarios al equipo de finanzas antes del viernes.",
        "El Ayuntamiento de la ciudad informa a los vecinos de que las obras en la calle Mayor comenzarán el lunes y se prolongarán durante tres semanas. Se ruega disculpen las molestias.",
        "Los empleados que no elijan un plan durante el periodo de inscripción mantendrán su cobertura actual. Si tiene preguntas, comuníquese con Recursos Humanos.",
        "Este aviso explica sus derechos según la ley. Usted puede tener derecho a doce semanas de licencia sin sueldo cada año para cuidar a un familiar.",
        "La biblioteca estará cerrada el jueves por mantenimiento. Los libros prestados se pueden devolver en el buzón que está junto a la puerta principal, y no habrá multas durante esos días.",
        # "si" was Italian-only and "será" Portuguese-only: this abstained.
        "Si tiene alguna pregunta, no dude en ponerse en contacto con nosotros. Será un placer ayudarle "
        "con todos los trámites y las dos solicitudes pendientes.",
    ],
    "fr": [
        "Le rapport trimestriel est joint pour votre examen. Veuillez envoyer vos commentaires à l'équipe des finances avant vendredi.",
        "La mairie informe les habitants que les travaux de la rue principale commenceront lundi et dureront trois semaines. Nous vous prions de nous excuser pour la gêne occasionnée.",
        "Les employés qui ne choisissent pas de régime pendant la période d'inscription conserveront leur couverture actuelle. Pour toute question, contactez les ressources humaines.",
        "Cet avis explique vos droits en vertu de la loi. Vous pouvez avoir droit à douze semaines de congé non payé chaque année pour prendre soin d'un membre de votre famille.",
        "La bibliothèque sera fermée jeudi pour des travaux d'entretien. Les livres empruntés peuvent être déposés dans la boîte située à côté de l'entrée principale.",
        # "à"-heavy French: "à" was listed only as Portuguese, and this page
        # came back lang="pt" (10 Portuguese votes to 2) and was charged.
        "Programme de la journée portes ouvertes. Accueil à 9 h à la mairie, visite du musée à 11 h, "
        "déjeuner à midi à la cantine scolaire. Atelier de peinture à 14 h à la bibliothèque, puis retour "
        "à la gare à 17 h. Rendez-vous à l'entrée principale. Inscription gratuite à l'accueil.",
        "Vous êtes invités à la fête de fin d'année, samedi à 18 h à la salle des fêtes. "
        "Merci de confirmer votre présence avant le 10 juin à l'adresse ci-dessous.",
        "Si vous avez des questions, n'hésitez pas à nous contacter. Nous répondrons à toutes les demandes "
        "dans un délai de deux jours ouvrables.",
    ],
    "de": [
        "Der Quartalsbericht ist zur Überprüfung beigefügt. Bitte senden Sie Ihre Kommentare bis Freitag an das Finanzteam.",
        "Die Stadtverwaltung informiert die Anwohner, dass die Bauarbeiten in der Hauptstraße am Montag beginnen und drei Wochen dauern werden. Wir bitten um Ihr Verständnis.",
        "Mitarbeiter, die während der Anmeldefrist keinen Tarif wählen, behalten ihren aktuellen Versicherungsschutz. Bei Fragen wenden Sie sich bitte an die Personalabteilung.",
        "Diese Mitteilung erklärt Ihre Rechte nach dem Gesetz. Sie haben möglicherweise Anspruch auf bis zu zwölf Wochen unbezahlten Urlaub pro Jahr, um einen Angehörigen zu pflegen.",
        "Die Bibliothek ist am Donnerstag wegen Wartungsarbeiten geschlossen. Ausgeliehene Bücher können in den Briefkasten neben dem Haupteingang eingeworfen werden.",
    ],
    "it": [
        "Il rapporto trimestrale è allegato per la vostra revisione. Si prega di inviare i commenti al team finanziario entro venerdì.",
        "Il Comune informa i cittadini che i lavori nella via principale inizieranno lunedì e dureranno tre settimane. Ci scusiamo per il disagio.",
        "I dipendenti che non scelgono un piano durante il periodo di iscrizione manterranno la loro copertura attuale. Per domande, contattare le risorse umane.",
        "Questo avviso spiega i vostri diritti ai sensi della legge. Potreste avere diritto a dodici settimane di congedo non retribuito ogni anno per assistere un familiare.",
        "La biblioteca sarà chiusa giovedì per lavori di manutenzione. I libri in prestito possono essere restituiti nella cassetta accanto all'ingresso principale.",
    ],
    "pt": [
        "O relatório trimestral está anexado para sua revisão. Por favor, envie seus comentários para a equipe de finanças até sexta-feira.",
        "A Câmara Municipal informa os moradores de que as obras na rua principal começarão na segunda-feira e durarão três semanas. Pedimos desculpa pelo incómodo causado a todos os cidadãos.",
        "Os funcionários que não escolherem um plano durante o período de inscrição manterão a sua cobertura atual. Em caso de dúvidas, entre em contato com os Recursos Humanos.",
        "Este aviso explica os seus direitos nos termos da lei. Você pode ter direito a doze semanas de licença não remunerada por ano para cuidar de um familiar.",
        "A biblioteca estará fechada na quinta-feira para manutenção. Os livros emprestados podem ser devolvidos na caixa ao lado da entrada principal.",
        # "todos/todas/porque" were Spanish-only.
        "Todas as inscrições devem ser feitas até sexta-feira. Porque o número de vagas é limitado, "
        "recomendamos que você se inscreva o quanto antes.",
    ],
    "nl": [
        "Het kwartaalrapport is bijgevoegd voor uw beoordeling. Stuur uw opmerkingen voor vrijdag naar het financiële team.",
        "De gemeente informeert de bewoners dat de werkzaamheden in de hoofdstraat op maandag beginnen en drie weken zullen duren. Wij vragen uw begrip voor de overlast.",
        "Medewerkers die tijdens de inschrijvingsperiode geen plan kiezen, behouden hun huidige dekking. Neem bij vragen contact op met de afdeling Personeelszaken.",
        "Deze kennisgeving legt uw rechten volgens de wet uit. U heeft mogelijk recht op twaalf weken onbetaald verlof per jaar om voor een familielid te zorgen.",
        "De bibliotheek is donderdag gesloten voor onderhoud. Geleende boeken kunnen worden ingeleverd in de brievenbus naast de hoofdingang.",
    ],
}
MUST_ABSTAIN = {
    "Swedish (no word list)": "Kvartalsrapporten bifogas för din granskning. Skicka dina kommentarer till finansteamet senast på fredag.",
    "Tagalog (no word list)": "Nakalakip ang quarterly report para sa inyong pagsusuri. Ipadala ang inyong mga komento sa finance team bago ang Biyernes.",
    "lorem ipsum": "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.",
    "half English, half French": "The report is attached. Le rapport est joint. Please review by Friday. Veuillez le lire avant vendredi.",
    "a six-word snippet": "Informe de ventas para el equipo.",
    "a two-word title": "Annual Report",
    "numbers only": "2023 410 12% 2024 455 11%",
    "Spanish made only of words it shares with French": (
        "La casa de la abuela está en la calle de la iglesia, cerca de la plaza de la ciudad. "
        "La oficina de la empresa está en la avenida de la paz."
    ),
}

# (original text, target) -> expected link text, or None for "refused".
LINKS = [
    ("click here", "https://example.com/reports/annual-report-2025.pdf", "Annual report 2025 (PDF)"),
    ("click here", "/docs/benefits-guide.pdf", "Benefits guide (PDF)"),
    ("read more", "https://blog.example.com/2026/09/why-we-moved-to-rust", "Why we moved to rust"),
    ("here", "https://cdn.example.org/files/2026-employee-handbook.pdf", "2026 employee handbook (PDF)"),
    ("click here", "https://example.com/privacy-policy", "Privacy policy"),
    ("click here", "https://example.com/terms%20of%20service.pdf", "Terms of service (PDF)"),
    ("click here", "https://example.com/reports/q3-2025-financial-results-v2-final.pdf", "Q3 2025 financial results (PDF)"),
    ("click here", "#section-2", None),
    ("click here", "mailto:hr@example.com", None),
    ("click here", "tel:+18005551212", None),
    ("click here", "javascript:void(0)", None),
    ("click here", "", None),
    ("click here", None, None),
    ("click here", "_Toc12345", None),
    ("click here", "https://example.com/", None),
    ("www.example.com", "https://www.example.com", None),
    ("click here", "https://www.irs.gov/pub/irs-pdf/f1040.pdf", None),
    ("read more", "https://example.com/article?id=8812", None),
    ("https://example.com/reports/q3.pdf", "https://example.com/reports/q3.pdf", None),
    ("download", "https://example.com/downloads/setup-v2.3.1-win64.zip", None),
    ("more info", "https://www.ssa.gov/benefits/retirement/planner/agereduction.html", None),
    ("learn more", "https://www.youtube.com/watch?v=dQw4w9WgXcQ", None),
    ("link", "http://192.168.1.10:8080/admin", None),
    ("click here", "https://localhost:3000/dashboard-home", None),
    ("click here", "https://example.com/click-here.html", None),
    ("click here", "https://example.com/index.html", None),
    ("click here", "https://example.com/wp-content/uploads/2024/03/IMG_2041.jpg", None),
    ("click here", "https://example.com/a/b/3f9a8c2e1b7d4e6f9a0b1c2d3e4f5a6b.pdf", None),
]

# Suggestions from ANY provider that must be refused / allowed.
LINK_SUGGESTIONS = [
    ("Read more about click here", "click here", "/docs/x", False),
    ("Read more about #section-2", "click here", "#section-2", False),
    ("Read more about hr@example.com", "click here", "mailto:hr@example.com", False),
    ("Read more about +18005551212", "click here", "tel:+18005551212", False),
    ("Read more about example.com", "click here", "https://example.com/x", False),
    ("Visit example.com", "click here", "https://example.com/", False),
    ("Read more about void(0)", "click here", "javascript:void(0)", False),
    ("mailto:hr@example.com", "click here", "mailto:hr@example.com", False),
    ("click here", "click here", "/x", False),
    ("Call +18005551212", "click here", "tel:+18005551212", False),
    ("Call 1-800-555-1212", "click here", "tel:+18005551212", False),
    ("Download", "click here", "https://example.com/r.pdf", False),
    ("PDF", "click here", "https://example.com/r.pdf", False),
    ("Link to page", "click here", "https://example.com/r", False),
    ("Call the benefits hotline", "click here", "tel:+18005551212", True),
    ("Download the regional revenue report", "click here", "https://example.com/r.pdf", True),
    ("Email the benefits office", "click here", "mailto:hr@example.com", True),
    ("Annual report 2025 (PDF)", "click here", "https://example.com/annual-report-2025.pdf", True),
]

# (caption, caption_source) -> expected alt, or None for "refused".
CAPTIONS = [
    ("Figure 2: Revenue by region", "figcaption", "Revenue by region"),
    ("Figure 2: Revenue by region", None, "Revenue by region"),
    ("Figure 2: Quarterly revenue by region, FY2025", "caption_style", "Quarterly revenue by region, FY2025"),
    ("Figure 2.1 Quarterly revenue", None, "Quarterly revenue"),
    ("Abbildung 4: Umsatz nach Region", None, "Umsatz nach Region"),
    ("Company logo", "title", "Company logo"),
    ("The CEO addressing staff at the 2026 town hall", "figcaption", "The CEO addressing staff at the 2026 town hall"),
    ("Figure 1 shows the new org chart:", "caption_style", "Figure 1 shows the new org chart"),
    # Near the picture, not written for it — whatever it says.
    ("Figure 2: Revenue by region", "preceding_text", None),
    ("Revenue grew in every region.", "preceding_text", None),
    ("The CEO addressing staff at the 2026 town hall", "own_paragraph", None),
    # ...unless the picture's own paragraph opens with its figure label.
    ("Figure 3: Organizational chart of the department", "own_paragraph", "Organizational chart of the department"),
    ("As Figure 3 shows, the department grew", "own_paragraph", None),
    ("Manager email: Click or tap here to enter text.", "preceding_text", None),
    ("Quarterly revenue by region", None, None),
    # Written near the picture but not a description.
    ("Home About Contact Login", "figcaption", None),
    ("Home | About | Contact", "figcaption", None),
    ("Posted on September 12, 2026 by admin", "figcaption", None),
    ("September 12, 2026", "figcaption", None),
    ("Photo credit: Jane Doe", "figcaption", None),
    ("https://example.com/photos/IMG_2041.png", "figcaption", None),
    ("IMG_2041.png", "title", None),
    ("Click to enlarge", "title", None),
    # A title attribute a CMS filled from the upload: slug, stock id, app
    # default name. Each was written as alt, charged, and then re-scanned as
    # fixed.
    ("sunset-beach-2", "title", None),
    ("hero-banner-v2", "title", None),
    ("shutterstock_123456789", "title", None),
    ("Untitled design (3)", "title", None),
    ("banner_final", "title", None),
    ("team-photo-2023-web", "title", None),
    ("logo-color-rgb", "title", None),
    ("AdobeStock_389201", "title", None),
    ("iStock-1162893421", "title", None),
    ("pexels-photo-3184291", "title", None),
    ("WhatsApp Image 2024-03-01 at 10.15.22", "title", None),
    ("Screen Shot 2024-05-12 at 10.32.11 AM", "title", None),
    ("PXL_20240301_123456", "title", None),
    ("Team 20230415", "title", None),
    ("Sunset", "title", None),
    # ...the same names in a figcaption are no description either.
    ("shutterstock_123456789", "figcaption", None),
    ("sunset-beach-2", "figcaption", None),
    # A title that is a phrase of words is still used, in any script.
    ("Mayor Jane Smith cuts the ribbon at the new library", "title", "Mayor Jane Smith cuts the ribbon at the new library"),
    ("Map of Europe", "title", "Map of Europe"),
    ("COVID-19 vaccination clinic at the town hall", "title", "COVID-19 vaccination clinic at the town hall"),
    ("東京タワーの夜景", "title", "東京タワーの夜景"),
    ("Photo 12 shows the mayor opening the library", "figcaption", "Photo 12 shows the mayor opening the library"),
    ("Wi-Fi coverage map for the main campus", "figcaption", "Wi-Fi coverage map for the main campus"),
    ("Figure 3", "figcaption", None),
    ("", "figcaption", None),
    # A label with nothing after it is still only a label.
    ("Figure 1.", "figcaption", None),
    ("Fig. 3", "caption_style", None),
    ("Chart 2:", "figcaption", None),
    ("Figure IV", "figcaption", None),
    # Page furniture set in the Caption style is not a description.
    ("Continued on next page", "caption_style", None),
    ("(continued)", "caption_style", None),
    ("See figure 3 below", "caption_style", None),
    # A picture of a table captioned as a table: the label is stripped too.
    ("Table 1: Revenue by region", "caption_style", "Revenue by region"),
    ("Figura 2: Ingresos por región", "figcaption", "Ingresos por región"),
]

TITLES_FROM_FILENAME = [
    ("q3-financial_report v2.docx", "Q3 Financial Report"),
    ("annual_budget.pdf", "Annual Budget"),
    ("employee-handbook-2026.docx", "Employee Handbook 2026"),
    ("20240912_meeting_notes.docx", "Meeting Notes"),
    ("Microsoft Word - Annual Budget Memo.docx", "Annual Budget Memo"),
    ("IMG_2041.pdf", None),
    ("DOC-20240912-WA0003.docx", None),
    ("final_FINAL_v3.pdf", None),
    ("2026-09-12.pdf", None),
    ("scan0001.pdf", None),
    ("Document1.docx", None),
    ("untitled (3).pdf", None),
    ("Copy of Copy of budget.xlsx", None),
    ("Microsoft Word - memo.docx.pdf", None),
    ("invoice-8812.pdf", None),
    ("Screenshot 2026-09-12 at 10.31.44.png.pdf", None),
    ("resume.pdf", None),
    # A format typed into the name is not part of the title; "of" inside a
    # name is kept (only a leading "Copy of" is noise).
    ("html_deep_nesting.html", "Deep Nesting"),
    ("Annual Report of the Board.docx", "Annual Report of the Board"),
    ("report_pdf_final.pdf", None),
]
TITLES_FROM_HEADING = [
    ("Annual Report 2025", "Annual Report 2025"),
    ("Employee Benefits Guide", "Employee Benefits Guide"),
    ("1", None),
    ("I.", None),
    ("Introduction", None),
    ("Contents", None),
    ("Chapter 1", None),
    ("\x00:\x00L\x00Q\x00W", None),
    # The heading of one numbered part names that part, not the document.
    ("Topic 1: Accessibility programme", None),
    ("Chapter 3 - Methods", None),
    ("Part II. Findings", None),
    # ...but "Section 508" is a law, not a numbered section.
    ("Section 508 Compliance Report", "Section 508 Compliance Report"),
    ("Section 508: A Guide for Agencies", "Section 508: A Guide for Agencies"),
    ("Section 508", "Section 508"),
]

TABLE_CAPTIONS = [
    ("Quarterly revenue by region", ["Region", "Q1"], True),
    ("Permits issued by district, FY2026", ["District", "Permits"], True),
    ("Data table", [], False),
    ("Table 1", [], False),
    ("Table: Region, Q1, Q2", ["Region", "Q1", "Q2"], False),
    ("Region, Q1 and Q2", ["Region", "Q1", "Q2"], False),
    ("Table: 2023, 410, 12%", ["2023", "410", "12%"], False),
]


def _cells(*texts):
    from app.models.accessibility import ContentKind, NodeContent, NodeMetadata, TableCellNode, TableCellType, TableHeaderScope

    return [
        TableCellNode(
            id=f"c{i}",
            cell_type=TableCellType.DATA,
            header_scope=TableHeaderScope.NONE,
            content=NodeContent(kind=ContentKind.TEXT, text=t) if t else NodeContent(kind=ContentKind.NONE),
            metadata=NodeMetadata(),
            children=[],
            accessibility_flags=[],
        )
        for i, t in enumerate(texts)
    ]


HEADER_ROWS = [
    (("Region", "Q1", "Q2"), [("North", "10", "20")], True),
    (("Region", "2023", "2024"), [("North", "120", "130")], True),
    (("2021", "2022", "2023"), [("10", "20", "30")], True),
    (("No.", "Item", "Qty"), [("1", "Pens", "40")], True),
    (("2023", "410", "12%"), [("2024", "455", "11%")], False),
    (("North", "120", "9%"), [("South", "140", "11%")], False),
    (("2023", "Pens", "North"), [("2024", "Paper", "South")], False),
    (("Emergency contact", ""), [("Phone", "")], False),
    (("Name:", "Jane"), [("Phone:", "555")], False),
    (("Revenue grew in every region.", "Yes"), [("x", "y")], False),
    (("Region", "Q1"), [], False),
    (("$1,200", "$900"), [("$800", "$700")], False),
    # Promoted only on positive evidence: column names over typed values, or a
    # row made of words people name columns with.
    (("Name", "Email", "Phone"), [("Ada", "ada@example.com", "555-0100")], True),
    (("Term", "Definition"), [("Alt text", "A short description of a picture")], True),
    (("Item", "Amount"), [("Travel", "12,500"), ("Supplies", "3,100")], True),
    (("Permit", "Fee", "Turnaround"), [("Building", "$240", "10 days"), ("Event", "$60", "3 days")], True),
    (("Indicator", "2022", "2024", "Change"), [("Uninsured rate", "9.1%", "7.4%", "-1.7")], True),
    (("Date received", "Applicant name", "Status"), [("Sept 3", "J. Ortiz", "Open")], True),
    (("Monday", "Tuesday", "Wednesday"), [("Intake", "Training", "Site visits")], True),
    (("Programme", "Budget (USD)"), [("Outreach", "120,000")], True),
    (("Employee", "Department", "Start date"), [("A. Rivera", "Finance", "March")], True),
    # Numbers written the way other scripts write them are still numbers.
    (("اللغة", "عدد المتحدثين"), [("العربية", "٤٢٠ مليون"), ("日本語", "1億2500万")], True),
    # A first row of other words is as likely to be the first row of DATA.
    (("Alice", "Engineering", "Denver"), [("Bob", "Sales", "Austin")], False),
    (("Monday", "Staff meeting", "Room 4"), [("Tuesday", "Training", "Room 2")], False),
    (("Accessibility", "Making content usable by everyone"), [("Alt text", "A short description")], False),
    # A label/value form whose first "row" is one filled-in field.
    (("Name", "Jane Doe"), [("Phone", "555-123-4567"), ("Email", "jane@example.com")], False),
    (("Salaries", "TBD"), [("Travel", "12,500")], False),
    (("Contact", "jane@example.com"), [("Office", "Room 4")], False),
]


def main() -> int:
    failures = 0
    # Check names quote Arabic/CJK cases; a Windows console (cp1252) must not
    # turn that into a crash of the smoke itself.
    try:
        sys.stdout.reconfigure(errors="backslashreplace")
    except Exception:  # pragma: no cover - non-reconfigurable stream
        pass

    def check(name: str, cond: bool, extra: str = "") -> None:
        nonlocal failures
        print(("PASS" if cond else "FAIL"), "-", name, extra if not cond else "")
        if not cond:
            failures += 1

    # ---- language --------------------------------------------------------
    for lang, samples in LANGUAGE_SAMPLES.items():
        for i, sample in enumerate(samples, start=1):
            code, conf, detail = detect_latin_language(sample)
            check(f"language: {lang} sample {i} -> {lang}", code == lang and conf >= 0.4, f"{code} {conf} [{detail}]")
    for name, sample in MUST_ABSTAIN.items():
        code, _conf, detail = detect_latin_language(sample)
        check(f"language: {name} -> abstain", code is None, f"{code} [{detail}]")
    h = HeuristicProvider()
    r = h.document_language({"sample": MUST_ABSTAIN["half English, half French"]})
    check("provider: an abstention is empty text with a reason", r.text == "" and r.confidence == 0.0 and bool((r.raw or {}).get("refusal")))
    for name, text, want in [
        ("japanese", "年次予算報告書 この報告書は本年度の予算執行状況をまとめたものです", "ja"),
        ("russian", "Годовой отчет о бюджете за текущий год и планы на следующий год", "ru"),
    ]:
        r = h.document_language({"sample": text})
        check(f"provider: script path unchanged ({name} -> {want})", r.text == want and r.confidence >= 0.4, f"{r.text} {r.confidence}")

    # ---- link text -------------------------------------------------------
    for original, target, want in LINKS:
        v = link_text_from_target(original, target)
        label = f"link: {original!r} -> {target!r}"
        check(f"{label} -> {want!r}", v.text == want, f"got {v.text!r}")
        if want is None:
            check(f"{label}: refusal has a plain reason", len(v.reason) > 20 and "http" not in v.reason.split("(")[0])
    for suggestion, original, target, ok in LINK_SUGGESTIONS:
        problem = vet_link_text(suggestion, original, target)
        check(f"vet link: {suggestion!r} {'allowed' if ok else 'refused'}", (problem is None) == ok, str(problem))
    r = h.link_text({"text": "click here", "target": "#section-2"})
    check("provider: link heuristic never answers 'Read more about …'", r.text == "", r.text)

    # ---- alt text --------------------------------------------------------
    for caption, source, want in CAPTIONS:
        v = alt_from_caption(caption, source)
        check(f"alt: {caption!r} [{source}] -> {want!r}", v.text == want, f"got {v.text!r} ({v.reason})")
    r = h.alt_text({"caption": "Home About Contact Login", "caption_source": "preceding_text", "label": "Image html-img-1"})
    check("provider: alt heuristic never pastes nearby text or a node id", r.text == "", r.text)
    r = h.alt_text({"label": "Uploaded image", "location": "image"})
    check("provider: alt heuristic with no caption abstains (no 'shown in' placeholder)", r.text == "", r.text)
    for alt, bad in [
        ("Uploaded image shown in image.", True),
        ("Image html-img-1 — Home About Contact Login", True),
        ("Image docx-img-2 — Manager email: Click or tap here to enter text.", True),
        ("Image page-3-img2 — Revenue grew in every region.", True),
        ("Image slide-2-img-1", True),
        ("Image of the document signing ceremony", False),
        ("Red car", False),
        ("Map of Europe", False),
        ("Acme Corp logo", False),
    ]:
        check(f"is_nondescriptive_alt({alt!r}) is {bad}", is_nondescriptive_alt(alt) is bad)
    for text, node_id, ok in [
        ("Bar chart of quarterly revenue by region", "html-img-1", True),
        ("Image html-img-1 — Home", "html-img-1", False),
        ("chart html-img-1", "html-img-1", False),
        ("https://example.com/a.png", None, False),
        ("Home About Contact Login", None, False),
        ("Posted on September 12, 2026 by admin", None, False),
    ]:
        check(f"vet alt: {text!r} {'allowed' if ok else 'refused'}", (vet_alt_text(text, node_id=node_id) is None) == ok)

    # ---- what a model sends back (no network: the call is replaced) --------
    from app.ai.offline_rules import clean_model_text
    from app.ai.semantic_inference import ClaudeProvider, OpenAIProvider

    for raw, want in [
        ("Alt text: A red car on a wet street", "A red car on a wet street"),
        ('"A red car on a wet street"', "A red car on a wet street"),
        ("Caption - Sales by region", "Sales by region"),
        ("A red car", "A red car"),
    ]:
        check(f"clean model text {raw!r} -> {want!r}", clean_model_text(raw) == want, clean_model_text(raw))
    for text in [
        "I'm sorry, but I can't describe this image.",
        "I cannot see the image.",
        "As an AI, I cannot view images.",
        "Unable to generate a description",
        "An image",
        "A photo.",
    ]:
        check(f"vet alt: model answer {text!r} refused", vet_alt_text(text) is not None)
    check("vet link: a model apology is refused",
          vet_link_text("I'm sorry, I can't determine where this link goes", "click here", "/x") is not None)
    check("vet caption: a model apology is refused",
          vet_table_caption("Sorry, I cannot determine what this table shows", ["Region"]) is not None)

    # The providers' real request/parse path, with urlopen replaced by a
    # canned answer: nothing leaves this process.
    import io as _io
    import json as _json
    import urllib.request as _ur

    canned = {"text": ""}

    class _Resp(_io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _fake_urlopen(request, timeout=None):
        url = getattr(request, "full_url", str(request))
        if "anthropic" in url:
            body = {"content": [{"type": "text", "text": canned["text"]}]}
        elif "openai" in url:
            body = {"choices": [{"message": {"content": canned["text"]}}]}
        else:
            raise AssertionError(f"unexpected network call in a smoke: {url}")
        return _Resp(_json.dumps(body).encode("utf-8"))

    real_urlopen = _ur.urlopen
    _ur.urlopen = _fake_urlopen
    try:
        for cls in (ClaudeProvider, OpenAIProvider):
            prov = cls(api_key="not-a-real-key")
            for answer, want in [
                ("en", "en"), ("pt-BR", "pt-br"), ("es.", "es"), ("Answer: fr", "fr"),
                ("It is English (en).", ""), ("The language is en", ""), ("English", ""),
            ]:
                canned["text"] = answer
                r = prov.document_language({"sample": "whatever"})
                check(f"{cls.__name__}: language answer {answer!r} -> {want or 'abstain'!r}", r.text == want, repr(r.text))
            canned["text"] = 'Alt text: "A red car on a wet street"'
            r = prov.alt_text({"image_b64": "AAAA", "image_mime": "image/png"})
            check(f"{cls.__name__}: a labelled, quoted alt answer is cleaned",
                  r.text == "A red car on a wet street", repr(r.text))
    finally:
        _ur.urlopen = real_urlopen

    # ---- titles ----------------------------------------------------------
    for filename, want in TITLES_FROM_FILENAME:
        v = title_from_filename(filename)
        check(f"title from file name {filename!r} -> {want!r}", v.text == want, f"got {v.text!r}")
    for heading, want in TITLES_FROM_HEADING:
        v = title_from_heading(heading)
        check(f"title from heading {heading!r} -> {want!r}", v.text == want, f"got {v.text!r}")

    # ---- table captions --------------------------------------------------
    r = h.table_caption({"headers": ["Region", "Q1", "Q2"], "sample": "North | 10 | 20"})
    check("provider: table caption heuristic abstains (no 'Table: <headers>')", r.text == "", r.text)
    for text, headers, ok in TABLE_CAPTIONS:
        check(f"vet caption: {text!r} {'allowed' if ok else 'refused'}", (vet_table_caption(text, headers) is None) == ok)
    # An AI caption may only state numbers the table itself contains.
    grid = "Region Q1 Q2 North 120 140 South 90 110"
    for text, ok in [
        ("Quarterly sales by region", True),
        ("Q1 and Q2 sales by region", True),
        ("Sales by region, FY2025", False),
        ("Revenue grew 12% in the North", False),
    ]:
        check(f"vet caption grounded in the table: {text!r} {'allowed' if ok else 'refused'}",
              (vet_table_caption(text, ["Region", "Q1", "Q2"], grid) is None) == ok,
              str(vet_table_caption(text, ["Region", "Q1", "Q2"], grid)))

    # ---- header rows -----------------------------------------------------
    from app.services.remediators.add_table_headers_executor import header_row_problem

    for first, body, ok in HEADER_ROWS:
        problem = header_row_problem(_cells(*first), [_cells(*row) for row in body])
        check(f"header row {first} over {body[:1]} {'promoted' if ok else 'refused'}", (problem is None) == ok, str(problem))

    print()
    print(f"{'OK' if failures == 0 else 'FAILED'}: smoke_semantic_rules_catalog ({failures} failure(s))")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
