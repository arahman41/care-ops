# Vendored CMS code vocabularies

Acquired 2026-07-18 for P2-3 (see
`docs/superpowers/specs/2026-07-18-p2-3-coding-eligibility-agent-design.md`).

Both sets are published by CMS in the public domain and are redistributable.
**CPT is not**, which is why it is absent here and why a CPT-shaped code
declared `CPT` is the one thing `shared/vocab.py::classify` returns
`unchecked` for.

These files hold **codes only, no descriptions.** `classify` needs
membership, nothing more. Keeping descriptions would cost roughly 2.5 MB for
no current consumer.

Every file here is reproducible from the CMS original with the commands
below. Nothing was hand-edited.

---

## ICD-10-CM

| | |
|---|---|
| File | `icd10cm_codes_2026.txt.gz` |
| Codes | 74,719 |
| Release | FY2026, April 1 2026 update |
| Source | `https://www.cms.gov/files/zip/april-1-2026-code-descriptions-tabular-order.zip` |
| Member used | `Code Descriptions/icd10cm_codes_2026.txt` |
| Encoding | UTF-8 |
| sha256 (decompressed) | `2a65a372ee0660fb812e2491a6a5d54212fcaccecf1cd508964c79a7744cf587` |

**FY choice.** FY2027 (effective 2026-10-01) is already published. FY2026 is
what is in effect as of this download, so it is what the agent is evaluated
against. Any bump to FY2027 must also bump `VOCAB_VERSION` and both pins,
and it will move the verified rate for reasons unrelated to the model.

**The `order` file is the wrong file.** The same zip ships
`icd10cm_order_2026.txt`, whose lines begin with a five-digit sequence
number (`00001 A00   0 Cholera ...`). Parsing it by first-token would load
74,000 sequence numbers as codes, and the sha256 pin would not notice
because it covers whatever was downloaded.

Source format: whitespace-delimited, code first, dotless.

```
A000    Cholera due to Vibrio cholerae 01, biovar cholerae
```

---

## HCPCS Level II

| | |
|---|---|
| File | `hcpcs_level2_2026q3.txt.gz` |
| Codes | 8,725 |
| Release | July 2026 quarterly (2026 Q3), file dated 2026-06-17 |
| Source | `https://www.cms.gov/files/zip/july-2026-alpha-numeric-hcpcs-file.zip` |
| Member used | `HCPC2026_JUL_ANWEB_06172026.txt` |
| Encoding | UTF-8 |
| sha256 (decompressed) | `d841e172cb20b718528eef465a8d19f36621570ae068fdaef983969dd810e9e2` |

**Level II only, verified.** "HCPCS" formally includes Level I, which *is*
CPT. A bundled file would make `classify` verify CPT codes by lookup, which
would dissolve the `unchecked` bucket and move the metric with no test
failing. Checked directly: the extracted set contains **zero** five-digit
numeric codes, and `99213` and `0001T` are both absent.

**Source format is FIXED WIDTH, not two-column.** This is the one place the
two vocabularies differ, and the reason `shared/vocab.py` carries two
parsers. Records are 293 characters with the code at `[0:5]`. The file also
contains shorter records for two-character modifiers (`A1`, `JK`, `E4`),
which are not codes and are filtered out by requiring `^[A-Z]\d{4}$`.

```
J1885001003Injection, ketorolac tromethamine, per 15 mg
```

**Cadence differs from ICD-10-CM.** HCPCS Level II updates quarterly while
ICD-10-CM updates annually, so the two will fall out of step. `VOCAB_VERSION`
is a single combined string and must be bumped on any change to either pin.

---

## Reproducing both files

Run from the repository root, with the two zips downloaded to `$SRC`.

```python
import gzip, hashlib, io, pathlib, re, zipfile

SRC = pathlib.Path("...")           # directory holding the two CMS zips
out = pathlib.Path("data/vocab")

def write_gz(path, payload):
    # mtime=0 so the artifact is byte-reproducible. The pin is over the
    # DECOMPRESSED content regardless; gzip headers are not byte-stable.
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as f:
        f.write(payload)
    path.write_bytes(buf.getvalue())
    return hashlib.sha256(payload).hexdigest()

# ICD-10-CM: whitespace-delimited, first token is the code.
zi = zipfile.ZipFile(SRC / "april-1-2026-code-descriptions-tabular-order.zip")
icd = sorted({l.split(None, 1)[0]
              for l in zi.read("Code Descriptions/icd10cm_codes_2026.txt")
                          .decode("utf-8").splitlines() if l.strip()})
write_gz(out / "icd10cm_codes_2026.txt.gz",
         ("\n".join(icd) + "\n").encode("utf-8"))

# HCPCS Level II: fixed width, code at [0:5], modifiers filtered out.
zh = zipfile.ZipFile(SRC / "july-2026-alpha-numeric-hcpcs-file.zip")
hc = sorted({l[0:5] for l in zh.read("HCPC2026_JUL_ANWEB_06172026.txt")
                                .decode("utf-8").splitlines()
             if len(l) == 293 and re.fullmatch(r"[A-Z]\d{4}", l[0:5])})
write_gz(out / "hcpcs_level2_2026q3.txt.gz",
         ("\n".join(hc) + "\n").encode("utf-8"))
```

Note that the CMS **HTML pages return 403** to automated fetchers, while the
direct file URLs above return 200. A failed page fetch is not evidence that
a URL is wrong.

---

## Incidental finding worth keeping

**6,761 of the 74,719 ICD-10-CM codes match `^[A-Z]\d{4}$`**, which is
exactly the HCPCS Level II shape (`M54.16` normalizes to `M5416`, shaped
identically to `J1885`).

That number is why HCPCS is vendored rather than shape-matched. An earlier
draft of the spec routed HCPCS by shape plus label, which would have let a
fabricated ICD-10-shaped code declared `HCPCS` reach `unchecked` and escape
the metric's denominator. 6,761 is the size of the collision space that
design would have been guessing across.
