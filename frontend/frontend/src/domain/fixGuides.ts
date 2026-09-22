/**
 * Per-issue, per-format MANUAL fix instructions.
 *
 * These back the public /fix/<slug> pages: someone googles "how to add alt
 * text to a PDF" and lands on a page that genuinely answers it, then sees
 * that we can do it automatically. The instructions must be correct whether
 * or not the reader ever becomes a customer — a page that wastes their time
 * is worse than no page.
 *
 * Rules for anything added here:
 *  - Only steps that are stable across versions of the app in question. UI
 *    labels move; these use the long-lived menu paths (Word's Alt Text pane,
 *    Acrobat's Reading Order tool, the Accessibility Checker) that have been
 *    stable for years.
 *  - Never describe a UI path you are not sure exists. A wrong instruction
 *    sends someone hunting through menus and costs us their trust.
 *  - The catalog (issueCatalog.ts) already carries what the issue IS, why it
 *    matters, and what OUR auto-fix does. This file adds only the by-hand
 *    procedure, per format. Entries are optional: a rule with no guide still
 *    renders a useful page from the catalog alone.
 */

export type FixFormat = "pdf" | "docx" | "pptx" | "html";

export interface FormatGuide {
  /** App/context these steps apply to, e.g. "Adobe Acrobat Pro". */
  tool: string;
  steps: string[];
  /** Optional caveat — a limitation or a common way people get it wrong. */
  note?: string;
}

export type FixGuide = Partial<Record<FixFormat, FormatGuide>>;

export const FORMAT_LABELS: Record<FixFormat, string> = {
  pdf: "PDF",
  docx: "Microsoft Word",
  pptx: "Microsoft PowerPoint",
  html: "HTML / web page",
};

export const FIX_GUIDES: Record<string, FixGuide> = {
  MISSING_ALT_TEXT: {
    docx: {
      tool: "Microsoft Word",
      steps: [
        "Right-click the image and choose View Alt Text (older versions: Format Picture → Layout & Properties → Alt Text).",
        "Type a description of what the image conveys in this document — not the filename, and not \"image of\".",
        "If the image is purely decorative, tick Mark as decorative instead of writing a description.",
        "Repeat for every image, then run Review → Check Accessibility to confirm none are left.",
      ],
      note: "Word's Alt Text pane also offers \"Generate a description for me\". Treat it as a draft: it describes what is in the picture, not why the picture is in your document.",
    },
    pptx: {
      tool: "Microsoft PowerPoint",
      steps: [
        "Right-click the picture, chart or shape and choose View Alt Text.",
        "Describe what the visual tells the audience. For a chart, give the takeaway (\"Sales doubled in Q3\"), not a list of every data point.",
        "Mark backgrounds and purely ornamental shapes as decorative.",
        "Check the whole deck with Review → Check Accessibility.",
      ],
    },
    pdf: {
      tool: "Adobe Acrobat Pro",
      steps: [
        "Open the Accessibility tool and choose Reading Order.",
        "Draw a box around the image and click Figure, then right-click it and choose Edit Alternate Text.",
        "Type the description and click OK.",
        "Alternatively, open the Tags panel, find the <Figure> element, and set Alternate Text in its Object Properties.",
      ],
      note: "If the PDF has no tags at all, alt text has nowhere to live — tag the document first (see \"PDF has no tags\").",
    },
    html: {
      tool: "HTML",
      steps: [
        "Add an alt attribute to every <img>: <img src=\"chart.png\" alt=\"Quarterly revenue rose 12% in Q3\">.",
        "For decorative images use an EMPTY alt (alt=\"\") so screen readers skip them. Omitting alt entirely is not the same thing — that makes some screen readers read the filename.",
        "For images inside links, describe where the link goes, not the picture.",
      ],
    },
  },

  PDF_UNTAGGED: {
    pdf: {
      tool: "Adobe Acrobat Pro",
      steps: [
        "Best result: go back to the source file (Word, InDesign, etc.), fix headings and alt text there, and re-export using its built-in \"create tagged PDF\" option. Tagging afterwards is always a repair job.",
        "If you only have the PDF: open the Accessibility tool and choose Autotag Document.",
        "Open the Tags panel and check the result — auto-tagging routinely mislabels headings, tables and running headers.",
        "Use the Reading Order tool to correct element types and the order content is read in.",
        "Finish with Accessibility → Full Check and work through anything it flags.",
      ],
      note: "Autotag is a starting point, not a finish line. On a complex layout it commonly needs an hour or more of hand correction per document.",
    },
  },

  SCANNED_DOCUMENT_NO_TEXT: {
    pdf: {
      tool: "Adobe Acrobat Pro",
      steps: [
        "Run Scan & OCR → Recognize Text → In This File. This adds a real text layer behind the page image.",
        "Proofread the result: OCR misreads tables, handwriting, columns and low-contrast scans.",
        "Then tag the document (see \"PDF has no tags\") — OCR gives you text, not structure.",
        "If you still have the original electronic file, use that instead. A scan-and-OCR round trip always loses fidelity.",
      ],
      note: "Until a scanned page has a text layer, a screen reader gets nothing from it at all — no headings, no alt text, no words.",
    },
  },

  DOCUMENT_TITLE_MISSING: {
    pdf: {
      tool: "Adobe Acrobat Pro",
      steps: [
        "File → Properties → Description, and fill in the Title field with the document's real title.",
        "Switch to the Initial View tab and set Show to \"Document Title\" so viewers display it instead of the filename.",
      ],
      note: "Both halves matter: the title is what a screen reader announces when the file opens, and the Initial View setting is what makes browsers and readers use it.",
    },
    docx: {
      tool: "Microsoft Word",
      steps: [
        "File → Info, then fill in the Title property in the right-hand panel (or Properties → Advanced Properties → Summary).",
        "Export to PDF with File → Save As → PDF and the title carries across.",
      ],
    },
    html: {
      tool: "HTML",
      steps: [
        "Give every page a unique, descriptive <title> in the <head>.",
        "Put the page-specific part first: \"Grant applications — City of Springfield\" beats \"City of Springfield — Grant applications\" in a long list of browser tabs and search results.",
      ],
    },
  },

  DOCUMENT_LANGUAGE_MISSING: {
    pdf: {
      tool: "Adobe Acrobat Pro",
      steps: [
        "File → Properties → Advanced, and set Reading Options → Language.",
        "For passages in another language, select them with the Reading Order tool and set the language on that element in the Tags panel.",
      ],
      note: "The language tag is what tells a screen reader which pronunciation rules to use. An English voice reading French is close to unintelligible.",
    },
    docx: {
      tool: "Microsoft Word",
      steps: [
        "Select all (Ctrl+A), then Review → Language → Set Proofing Language and choose the document's language.",
        "Select any foreign-language passages separately and set their language too.",
      ],
    },
    html: {
      tool: "HTML",
      steps: [
        "Set the language on the root element: <html lang=\"en\">.",
        "Mark inline exceptions with their own lang, e.g. <span lang=\"fr\">bon appétit</span>.",
      ],
    },
  },

  TABLE_MISSING_HEADERS: {
    docx: {
      tool: "Microsoft Word",
      steps: [
        "Click in the table, then on the Table Design tab tick Header Row.",
        "Open Table Layout → Properties → Row and tick \"Repeat as header row at the top of each page\". This is the part that actually marks the row as a header for assistive tech.",
        "Make sure the header row holds real labels, not blanks or \"Column 1\".",
      ],
      note: "Styling a row bold is not a header. The Repeat-as-header setting is what writes the structural marker.",
    },
    pdf: {
      tool: "Adobe Acrobat Pro",
      steps: [
        "Accessibility → Reading Order → Table Editor, or find the <Table> in the Tags panel.",
        "Select each cell in the top row, right-click → Table Cell Properties, and set Type to Header Cell with Scope = Column.",
        "Do the same with Scope = Row for a left-hand label column.",
      ],
    },
    html: {
      tool: "HTML",
      steps: [
        "Use <th> instead of <td> for header cells, inside a <thead>.",
        "Add scope: <th scope=\"col\"> for column headers, <th scope=\"row\"> for row labels.",
        "For a table with both, every data cell should be reachable from one column header and one row header.",
      ],
    },
  },

  HEADING_LEVEL_JUMP: {
    docx: {
      tool: "Microsoft Word",
      steps: [
        "Open View → Navigation Pane to see the current outline at a glance.",
        "Apply the built-in Heading 1/2/3 styles from the Home tab so levels descend one at a time — an H2 followed by an H4 leaves a hole in the outline.",
        "Never pick a heading level for its font size. Set the level by meaning, then restyle the level itself (right-click the style → Modify).",
      ],
    },
    html: {
      tool: "HTML",
      steps: [
        "Use <h1>–<h6> in descending order without skipping: an <h2> may be followed by <h3>, not <h4>.",
        "One <h1> per page, naming what the page is.",
        "If a heading looks wrong at its correct level, fix it with CSS rather than changing the tag.",
      ],
    },
    pdf: {
      tool: "Adobe Acrobat Pro",
      steps: [
        "Open the Tags panel and read down the structure looking for a jump, e.g. <H1> straight to <H3>.",
        "Right-click the offending tag → Properties and change its Type to the correct heading level.",
      ],
    },
  },

  DOCUMENT_NO_HEADINGS: {
    docx: {
      tool: "Microsoft Word",
      steps: [
        "Apply the built-in Heading styles (Home tab) to every section title instead of bolding and enlarging text by hand.",
        "Check the result in View → Navigation Pane: it should read like a table of contents.",
      ],
      note: "Headings are how screen-reader users skim. With none, the only way through a long document is to listen to all of it, start to finish.",
    },
    html: {
      tool: "HTML",
      steps: [
        "Mark section titles with <h1>–<h6> rather than styled <div>s or <p>s.",
        "Keep the order meaningful: the heading levels are the document's outline.",
      ],
    },
  },

  TEXT_STYLED_AS_HEADING: {
    docx: {
      tool: "Microsoft Word",
      steps: [
        "Select the big/bold line and apply the matching Heading style from the Home tab.",
        "If you liked the old look, modify the heading style (right-click → Modify) rather than reverting to manual formatting.",
      ],
      note: "Assistive tech reads structure, not size. A 24pt bold line that isn't a Heading style is invisible to the outline a screen-reader user navigates by.",
    },
    html: {
      tool: "HTML",
      steps: [
        "Replace <p class=\"big-bold\"> with a real heading element at the right level.",
        "Move the visual styling to CSS on that heading.",
      ],
    },
  },

  LINK_TEXT_NON_DESCRIPTIVE: {
    docx: {
      tool: "Microsoft Word",
      steps: [
        "Right-click the link → Edit Hyperlink, and rewrite Text to display so it names the destination: \"2026 grant application form\" instead of \"click here\".",
        "Avoid pasting bare URLs as link text — a screen reader may read the whole string character by character.",
      ],
      note: "Screen-reader users often pull up a list of every link on the page. Out of context, twelve links all reading \"click here\" are useless.",
    },
    html: {
      tool: "HTML",
      steps: [
        "Make the link text itself describe the destination: <a href=\"/apply\">Apply for a permit</a>.",
        "If the visible text must stay short, add an aria-label that CONTAINS the visible words, e.g. text \"Apply\" with aria-label=\"Apply for a permit\".",
        "Never use the same link text for two different destinations on one page.",
      ],
    },
    pdf: {
      tool: "Adobe Acrobat Pro",
      steps: [
        "In the Tags panel find the <Link> element and set its Alternate Text to describe the destination.",
        "Better: fix the wording in the source document and re-export.",
      ],
    },
  },

  LINK_NAME_MISSING: {
    html: {
      tool: "HTML",
      steps: [
        "Give every link text content, or an accessible name via aria-label.",
        "Icon-only links need one: <a href=\"/cart\" aria-label=\"Shopping cart\"><svg …></a>.",
        "An image inside a link supplies the name through its alt attribute.",
      ],
    },
  },

  FORM_FIELD_UNLABELED: {
    pdf: {
      tool: "Adobe Acrobat Pro",
      steps: [
        "Open Prepare Form, then double-click each field.",
        "On the General tab, fill in Tooltip — that is the accessible name a screen reader announces.",
        "Name fields for what the user must enter (\"Date of birth\"), not their internal id (\"txt_dob_1\").",
      ],
    },
    docx: {
      tool: "Microsoft Word",
      steps: [
        "Select the content control, then Developer → Properties and fill in the Title field.",
        "If the Developer tab isn't visible: File → Options → Customize Ribbon and tick Developer.",
      ],
    },
    html: {
      tool: "HTML",
      steps: [
        "Pair every input with a <label for=\"…\"> matching the input's id.",
        "A placeholder is not a label — it disappears when typing starts and many screen readers ignore it.",
        "For inputs that genuinely cannot show a visible label, use aria-label.",
      ],
    },
  },

  LIST_STRUCTURE_INVALID: {
    docx: {
      tool: "Microsoft Word",
      steps: [
        "Select the lines and apply the Bullets or Numbering button on the Home tab.",
        "Delete the typed \"-\" or \"1.\" characters — the list formatting supplies them.",
      ],
      note: "A typed hyphen makes something LOOK like a list. Real list markup is what tells a screen reader \"list of 5 items\" and lets the user skip it.",
    },
    html: {
      tool: "HTML",
      steps: [
        "Wrap the items in <ul> (unordered) or <ol> (ordered) with each item in <li>.",
        "Don't fake a list with <br> or <p>• …</p>.",
      ],
    },
    pdf: {
      tool: "Adobe Acrobat Pro",
      steps: [
        "In the Tags panel, group the items under an <L> tag, with each item as <LI> containing <LBody>.",
        "This is fiddly by hand — it's usually faster to fix the list in the source document and re-export.",
      ],
    },
  },

  SLIDE_TITLE_MISSING: {
    pptx: {
      tool: "Microsoft PowerPoint",
      steps: [
        "Open View → Outline View: any slide with no title shows as blank there.",
        "Add a title using the slide's Title placeholder (Home → Layout gives you one if the slide lacks it).",
        "To keep a title off the visible slide, add it in the placeholder and then drag it off-canvas, or use Home → Arrange → Selection Pane to hide it — the text stays available to screen readers.",
      ],
      note: "Slide titles are how screen-reader users navigate a deck — the equivalent of headings in a document.",
    },
  },

  READING_ORDER_AMBIGUOUS: {
    pdf: {
      tool: "Adobe Acrobat Pro",
      steps: [
        "Accessibility → Reading Order, then click \"Show Order Panel\" to see the numbered sequence content is read in.",
        "Drag entries in the Order panel until they follow the visual reading order — down each column of a two-column layout, not across.",
        "Verify with View → Read Out Loud, or by tabbing through with a screen reader.",
      ],
      note: "This is the single most common wreck in a multi-column PDF: the reader alternates between two unrelated columns, sentence by sentence.",
    },
    pptx: {
      tool: "Microsoft PowerPoint",
      steps: [
        "Home → Arrange → Selection Pane lists every object on the slide.",
        "Screen readers read the list from the BOTTOM up, so order it accordingly (PowerPoint's newer Reading Order pane shows it top-down instead — use whichever your version has).",
      ],
    },
  },

  LOW_CONTRAST_TEXT: {
    html: {
      tool: "HTML / CSS",
      steps: [
        "WCAG AA needs a contrast ratio of at least 4.5:1 for normal text, 3:1 for large text (18pt, or 14pt bold).",
        "Darken the text rather than lightening the background — it usually preserves the design better.",
        "Check the pair in a contrast checker before committing to it.",
      ],
    },
    docx: {
      tool: "Microsoft Word / PowerPoint",
      steps: [
        "Select the text and pick a darker colour from the Font Color menu.",
        "Watch out for light grey body text and for coloured text on a coloured table shading — both are common failures.",
      ],
      note: "Grey-on-white body text is the most common contrast failure in office documents; it usually lands around 3:1 when it needs 4.5:1.",
    },
  },

  IFRAME_TITLE_MISSING: {
    html: {
      tool: "HTML",
      steps: [
        "Add a title attribute naming the embedded content: <iframe title=\"Budget hearing video\" …>.",
        "Make each title unique on the page — \"YouTube video player\" three times tells a user nothing.",
        "Add title=\"\" plus aria-hidden=\"true\" only for a genuinely decorative or offscreen frame.",
      ],
    },
  },

  INPUT_AUTOCOMPLETE_MISSING: {
    html: {
      tool: "HTML",
      steps: [
        "Add the autocomplete attribute matching the data: autocomplete=\"email\", \"tel\", \"given-name\", \"postal-code\", \"street-address\".",
        "Use the exact token names from the HTML spec — invented values do nothing.",
      ],
      note: "This is WCAG 2.1's Identify Input Purpose. It lets browsers and assistive tech fill fields for people with motor or memory impairments, and it shortens every form for everyone else.",
    },
  },

  LABEL_IN_NAME_MISMATCH: {
    html: {
      tool: "HTML",
      steps: [
        "Make the accessible name CONTAIN the visible text. A button showing \"Send\" must not be labelled aria-label=\"Submit form\".",
        "Best fix is usually to delete the aria-label entirely and let the visible text be the name.",
        "If you need extra context, prefix or suffix the visible words: aria-label=\"Send message to support\" on a button reading \"Send\".",
      ],
      note: "Someone using voice control says what they see. If the name doesn't contain the visible words, saying \"click Send\" does nothing.",
    },
  },

  POSITIVE_TABINDEX: {
    html: {
      tool: "HTML",
      steps: [
        "Remove tabindex values above 0. A positive tabindex yanks that element to the front of the tab order for the WHOLE page.",
        "Use tabindex=\"0\" to make a custom control focusable in its natural position, and tabindex=\"-1\" for programmatic focus only.",
        "If the tab order is wrong, fix the DOM order instead.",
      ],
    },
  },

  DECORATIVE_IMAGE_WITH_ALT: {
    html: {
      tool: "HTML",
      steps: [
        "Give a purely decorative image an empty alt: alt=\"\". That tells screen readers to skip it entirely.",
        "Don't write alt=\"decorative\" or alt=\"spacer\" — that is noise read aloud.",
      ],
    },
    docx: {
      tool: "Microsoft Word / PowerPoint",
      steps: [
        "Right-click the image → View Alt Text and tick Mark as decorative. Any existing description is discarded.",
      ],
    },
  },

  TABLE_CAPTION_MISSING: {
    html: {
      tool: "HTML",
      steps: [
        "Add a <caption> as the first child of the <table>, naming what the table shows.",
        "A caption is announced with the table, so a user landing on it knows what they've found.",
      ],
    },
    docx: {
      tool: "Microsoft Word",
      steps: [
        "Click the table, then References → Insert Caption, and place it above the table.",
        "Word applies the Caption style, which is what carries through to an exported PDF.",
      ],
    },
  },

  ALT_TEXT_NOT_DESCRIPTIVE: {
    html: {
      tool: "Any format",
      steps: [
        "Replace filenames and placeholders (\"image1.png\", \"Picture 3\", \"chart\") with what the image actually conveys.",
        "Ask: if this image vanished, what would the reader need to be told? That sentence is the alt text.",
        "Skip \"image of\" / \"picture of\" — screen readers already announce that it's an image.",
      ],
    },
  },
};

/** URL slug for a rule id: MISSING_ALT_TEXT -> missing-alt-text. */
export function ruleSlug(ruleId: string): string {
  return ruleId.toLowerCase().replace(/_/g, "-");
}

/** Inverse of ruleSlug. */
export function slugToRuleId(slug: string): string {
  return slug.toUpperCase().replace(/-/g, "_");
}

/** Rules that get a public /fix page. Excludes internal/process flags that
 *  are not defects in the reader's document. */
export const GUIDE_EXCLUDED = new Set(["ANALYSIS_TRUNCATED"]);
