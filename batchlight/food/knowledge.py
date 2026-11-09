"""What testing has actually found in common foods, with sources.

Rules for this content (see docs/FOOD_SAFETY.md):

* Every finding names its source, year and evidence type, and links to it.
* "official" = a regulator's own data, a government release or a parliamentary
  reply; "study" = a peer-reviewed paper; "report" = credible news or NGO
  reporting of a test or regulator action; "provider" = a service provider's own
  published information, such as a laboratory's price list.
* Findings describe what was found in samples. They never claim that a brand,
  a pack or "most" of a food is adulterated, and never suggest a home test can
  prove adulteration.
"""

from __future__ import annotations

from dataclasses import dataclass, field

EVIDENCE_LABELS = {
    "official": "Official data",
    "study": "Peer-reviewed study",
    "report": "News or NGO report",
    "provider": "Published by the provider",
}


@dataclass(frozen=True)
class Finding:
    text: str
    source: str
    year: str
    evidence: str
    url: str


@dataclass(frozen=True)
class Topic:
    slug: str
    name: str
    also_called: str
    summary: str
    findings: tuple[Finding, ...]
    concern: str
    home_testing: str
    what_you_can_do: tuple[str, ...] = field(default_factory=tuple)
    search_terms: str = ""


HOME_TESTING_GENERAL = (
    "There is no reliable way to prove adulteration at home. FSSAI publishes simple home checks (its DART booklet) as an "
    "awareness tool, but no accuracy data for them has been published. A food-testing laboratory is the only way to confirm "
    "what is in a sample."
)

COMMON_ACTIONS = (
    "Prefer packaged food that shows an FSSAI licence or registration number, a batch number and a best-before date.",
    "Check the pack against recalls on this site, and keep the pack and receipt if something seems wrong.",
    "Report a suspicious product here and to FSSAI through the Food Safety Connect app.",
    "If you need certainty, have a sample tested at an accredited laboratory.",
)

TOPICS: tuple[Topic, ...] = (
    Topic(
        slug="overview-india",
        name="What India's food tests find",
        also_called="FSSAI surveillance results",
        summary=(
            "Food safety officers in India test well over a lakh samples a year. Around one in five fails a standard, but most "
            "failures are about quality or labelling. A much smaller share is judged unsafe."
        ),
        findings=(
            Finding(
                "In 2023-24, 33,808 of 170,513 food samples analysed (19.8%) did not conform to standards. Of these, 6,782 "
                "were classed as unsafe, 22,603 as sub-standard and 4,423 as having labelling defects.",
                "Rajya Sabha reply, Ministry of Health and Family Welfare",
                "2024",
                "official",
                "https://rsdebate.nic.in/bitstream/123456789/752772/1/PQ_266_17122024_U2531_p348_p350.pdf",
            ),
            Finding(
                "In 2025-26 (provisional), 40,023 of 223,808 samples analysed (about 17.9%) did not conform. The reply did "
                "not say how many were unsafe.",
                "Lok Sabha reply, reported by ANI",
                "2026",
                "report",
                "https://www.latestly.com/agency-news/business-news-over-40000-food-samples-found-unsafe-or-below-standards-in-fy26-only-1918-convictions-recorded-govt-7540033.html",
            ),
        ),
        concern="Unsafe food can cause acute illness or long-term harm. Sub-standard and mislabelled food cheats buyers.",
        home_testing=HOME_TESTING_GENERAL,
        what_you_can_do=COMMON_ACTIONS,
        search_terms="fssai samples unsafe substandard",
    ),
    Topic(
        slug="milk",
        name="Milk and dairy",
        also_called="doodh, paneer, khoya",
        summary=(
            "India's national milk survey found that contamination from animal feed and veterinary drugs was more common than "
            "deliberate toxic adulteration. Paneer substitutes sold as paneer are a current enforcement issue."
        ),
        findings=(
            Finding(
                "In FSSAI's national milk survey of 6,432 samples, aflatoxin M1 was above the limit in 368 (5.7%) and "
                "antibiotic residues in 77 (1.2%). Only 12 samples contained adulterants that could affect safety, such as "
                "hydrogen peroxide, detergent, urea or neutralisers.",
                "FSSAI National Milk Safety and Quality Survey",
                "2018",
                "official",
                "https://www.fssai.gov.in/upload/press_release/2018/11/5beaab0190f20Press_Release_Milk_Survey_Report_13_11_2018.pdf",
            ),
            Finding(
                "FSSAI noted that aflatoxin M1 reaches milk through contaminated cattle feed and fodder.",
                "FSSAI National Milk Safety and Quality Survey, final report",
                "2019",
                "official",
                "https://mail.fssai.gov.in/upload/uploadfiles/files/Report_Milk_Survey_NMQS_Final_18_10_2019.pdf",
            ),
            Finding(
                "Maharashtra's Food and Drug Administration found 109 of 308 paneer and dairy-substitute samples (35.4%) "
                "non-conforming between April 2025 and March 2026, including 30 judged unsafe.",
                "Maharashtra FDA, reported by the Free Press Journal",
                "2026",
                "report",
                "https://www.freepressjournal.in/amp/mumbai/maharashtra-govt-bans-analogue-paneer-for-one-year-after-354-per-cent-of-samples-fail-fda-quality-tests",
            ),
        ),
        concern=(
            "Aflatoxin M1 is a toxin from mould in animal feed. Antibiotic residues contribute to antimicrobial resistance. "
            "Substitute paneer made with vegetable fat is not unsafe in itself, but selling it as paneer misleads buyers."
        ),
        home_testing=HOME_TESTING_GENERAL,
        what_you_can_do=COMMON_ACTIONS,
        search_terms="milk dairy paneer ghee khoya curd aflatoxin antibiotic",
    ),
    Topic(
        slug="spices",
        name="Spices and masalas",
        also_called="masala, chilli powder, jeera, curry powder",
        summary=(
            "Spices have failed tests for pesticide residues, ethylene oxide and contamination, both in India and abroad. "
            "Results can differ by batch, so recalls usually name specific batches."
        ),
        findings=(
            Finding(
                "Of 4,054 spice samples FSSAI tested from May to early July 2024, 474 (about 12%) failed quality or safety "
                "standards. FSSAI said it had no breakdown by brand.",
                "FSSAI data obtained by Reuters under RTI, reported by Business Standard",
                "2024",
                "report",
                "https://www.business-standard.com/india-news/nearly-12-of-india-s-tested-spice-samples-fail-quality-safety-standards-124081900106_1.html",
            ),
            Finding(
                "Hong Kong's Centre for Food Safety found ethylene oxide in three MDH spice mixes and Everest Fish Curry "
                "Masala in April 2024, and Singapore ordered a recall of the Everest product.",
                "Hong Kong CFS and Singapore SFA, reported by Scroll",
                "2024",
                "report",
                "https://scroll.in/latest/1066928/hong-kong-authorities-recall-ban-sale-of-four-everest-mdh-spice-products-imported-from-india",
            ),
            Finding(
                "FSSAI then sampled spices across India and reported no ethylene oxide in the 28 MDH and Everest test reports "
                "it had received.",
                "FSSAI, reported by Business Standard",
                "2024",
                "report",
                "https://business-standard.com/companies/news/no-traces-of-ethylene-oxide-found-in-mdh-everest-spice-samples-fssai-124052101441_1.html",
            ),
            Finding(
                "FSSAI ordered a recall of one batch of Everest cumin powder (E080668761) for residues of the pesticides "
                "azoxystrobin and thiamethoxam above limits, and said the finding applied only to that batch.",
                "FSSAI, reported by Business Today",
                "2026",
                "report",
                "https://www.businesstoday.in/india/story/unsafe-fssai-orders-recall-of-everest-cumin-powder-batch-after-pesticide-residues-found-above-limits-559413-2026-10-04",
            ),
        ),
        concern=(
            "Ethylene oxide is classed by IARC as carcinogenic to humans. Pesticide residues above legal limits are not "
            "permitted in food sold in India."
        ),
        home_testing=HOME_TESTING_GENERAL,
        what_you_can_do=("Check the batch number on your pack against the recalls on this site.", *COMMON_ACTIONS[1:]),
        search_terms="spice masala chilli cumin jeera curry ethylene oxide pesticide mdh everest",
    ),
    Topic(
        slug="turmeric",
        name="Turmeric",
        also_called="haldi",
        summary=(
            "A peer-reviewed study found lead chromate, a bright yellow industrial pigment, added to turmeric in some markets. "
            "Most samples were within limits, but some were extremely contaminated."
        ),
        findings=(
            Finding(
                "Researchers tested 356 turmeric samples from 23 South Asian cities in 2020-21. About 14% had lead above "
                "2 micrograms per gram. Samples from Patna exceeded 1,000 micrograms per gram, against India's limit of 10.",
                "Forsyth et al., Science of the Total Environment",
                "2024",
                "study",
                "https://fsi.stanford.edu/node/36768",
            ),
            Finding(
                "Lead and chromium appeared in a near 1:1 ratio, pointing to deliberate addition of lead chromate as a colourant.",
                "Forsyth et al., Science of the Total Environment",
                "2024",
                "study",
                "https://pesquisa.bvsalud.org/bolivia/resource/es/mdl-39053552",
            ),
        ),
        concern=(
            "WHO says there is no level of lead exposure known to be without harmful effects. Children and pregnant women "
            "are most at risk."
        ),
        home_testing=(
            "Colour, smell or a water test cannot show whether turmeric contains lead. Only a laboratory test for heavy "
            "metals can."
        ),
        what_you_can_do=COMMON_ACTIONS,
        search_terms="turmeric haldi lead chromate heavy metal",
    ),
    Topic(
        slug="edible-oils",
        name="Edible oils",
        also_called="mustard oil, groundnut oil, blended oils",
        summary=(
            "FSSAI's national edible oil survey found that about one in four samples failed quality tests, while a small "
            "share failed safety tests."
        ),
        findings=(
            Finding(
                "Of 4,461 edible oil samples, 108 (2.42%) failed safety tests for aflatoxins, pesticide residues or heavy "
                "metals, 1,080 (24.2%) failed quality parameters, and 572 (12.8%) were misbranded on fortification claims.",
                "FSSAI All-India Edible Oil Survey",
                "2020",
                "official",
                "https://www.fssai.gov.in/upload/uploadfiles/files/Report_Edible_Oil_Survey_27_01_2022.pdf",
            ),
        ),
        concern="Quality failures can mean undeclared blending. Safety failures involve contaminants that can harm health.",
        home_testing=HOME_TESTING_GENERAL,
        what_you_can_do=COMMON_ACTIONS,
        search_terms="oil mustard groundnut sunflower edible blended",
    ),
    Topic(
        slug="honey",
        name="Honey",
        also_called="shahad",
        summary=(
            "Sugar syrups added to honey are hard to detect. Different laboratory methods have given conflicting results "
            "for the same brands, and brands have disputed findings."
        ),
        findings=(
            Finding(
                "The Centre for Science and Environment reported that 10 of 13 honey brands that passed Indian standards "
                "failed a German NMR test. Several brands disputed this and cited their own passing results.",
                "Centre for Science and Environment",
                "2020",
                "report",
                "https://www.downtoearth.org.in/news/food/cse-gives-fssai-information-about-its-probe-into-honey-adulteration-74527",
            ),
            Finding(
                "A study of 98 authentic Indian honeys found about 6% would fail FSSAI's isotope criteria, which are designed "
                "to detect cane and corn syrups.",
                "Foods (peer-reviewed journal)",
                "2025",
                "study",
                "https://mdpi-res.com/d_attachment/foods/foods-14-01289/article_deploy/foods-14-01289.pdf",
            ),
            Finding(
                "The UK Food Standards Agency advises that NMR results depend on a reference database of authentic honeys and "
                "should be used as a screening tool alongside other evidence.",
                "UK Food Standards Agency",
                "2020",
                "official",
                "https://www.food.gov.uk/research/honey-authenticity-the-nmr-issue",
            ),
        ),
        concern="Syrup-adulterated honey is mainly a fraud on buyers; it can also matter for people managing their sugar intake.",
        home_testing=(
            "Popular home tests, such as dropping honey in water or setting it alight, cannot detect sugar syrups. Even "
            "laboratory methods have limits, so no single test is conclusive."
        ),
        what_you_can_do=COMMON_ACTIONS,
        search_terms="honey shahad syrup sugar nmr",
    ),
    Topic(
        slug="food-colours",
        name="Artificial colours in street food",
        also_called="cotton candy, gobi manchurian, sweets",
        summary="State food safety departments have found banned or excess colours in popular street foods and banned them.",
        findings=(
            Finding(
                "Tamil Nadu banned cotton candy containing Rhodamine B, a textile dye, after a government laboratory found it. "
                "Karnataka found 106 of 171 gobi manchurian samples unsafe, mainly from tartrazine and sunset yellow, and "
                "Rhodamine B in 6 of 25 cotton candy samples.",
                "Tamil Nadu and Karnataka food safety departments, reported by The News Minute",
                "2024",
                "report",
                "https://www.thenewsminute.com/karnataka/karnataka-bans-sale-of-cotton-candy-gobi-manchurian-containing-rhodamine-b",
            ),
        ),
        concern="Rhodamine B is not permitted in food. Permitted colours are only allowed within limits and in certain foods.",
        home_testing="Bright colour alone does not prove a banned dye. Only laboratory analysis can identify the dye.",
        what_you_can_do=(
            "Be cautious with unusually bright street food, especially for children.",
            "Report vendors to your state food safety department or through the Food Safety Connect app.",
        ),
        search_terms="colour color dye rhodamine tartrazine cotton candy gobi manchurian sweets",
    ),
    Topic(
        slug="pesticide-residues",
        name="Pesticide residues",
        also_called="fruit, vegetables, grains, tea",
        summary="Government monitoring finds pesticide residues above Indian limits in a small share of samples.",
        findings=(
            Finding(
                "Of 86,401 food samples tested for pesticide residues in 2022-25, 2.8% exceeded FSSAI's maximum residue limits.",
                "Government statement to Parliament, reported by Medical Dialogues",
                "2025",
                "report",
                "https://health.medicaldialogues.in/health/fssai-finds-pesticide-residues-above-safe-limits-in-28-of-food-samples-153097",
            ),
            Finding(
                "FSSAI ordered Patanjali Foods to recall a batch of red chilli powder for pesticide residues above the limit, "
                "without issuing a public alert.",
                "FSSAI order, reported by ThePrint",
                "2025",
                "report",
                "https://theprint.in/india/patanjali-told-to-recall-4-tonnes-of-red-chilli-powder-from-market-but-no-public-alert-by-fssai/2461011",
            ),
        ),
        concern="Residues above legal limits are not permitted. Long-term health effects depend on the pesticide and the dose.",
        home_testing="Pesticide residues cannot be seen, smelled or tested for at home.",
        what_you_can_do=COMMON_ACTIONS,
        search_terms="pesticide residue fruit vegetable mrl chilli",
    ),
)

TOPICS_BY_SLUG = {topic.slug: topic for topic in TOPICS}

WHY_IT_MATTERS = Finding(
    "WHO estimated that unsafe food caused about 600 million illnesses and 420,000 deaths worldwide in one year, including "
    "about 150 million illnesses and 175,000 deaths in South-East Asia.",
    "WHO Foodborne Disease Burden Epidemiology Reference Group (Havelaar et al., PLOS Medicine)",
    "2015",
    "study",
    "https://pmc.ncbi.nlm.nih.gov/articles/4668832",
)

TESTING_FACTS = (
    Finding(
        "FSSAI has approved 88 rapid test kits and devices under its RAFT scheme, for screening and surveillance only.",
        "Press Information Bureau, Ministry of Health and Family Welfare",
        "2026",
        "official",
        "https://pib.gov.in/PressReleasePage.aspx?PRID=2292341",
    ),
    Finding(
        "States and union territories have deployed 305 mobile food testing labs (Food Safety on Wheels), which run simple "
        "tests for common adulterants.",
        "Press Information Bureau release (copy hosted by Legality Simplified)",
        "2025",
        "official",
        "https://www.legalitysimplified.com/wp-content/uploads/2025/12/PIB2197617.pdf",
    ),
    Finding(
        "At the pesticide residue laboratory of PJTAU, a public agricultural university in Telangana, a full "
        "multi-residue pesticide test is listed at about Rs 10,000 to Rs 14,000 and takes 7 to 10 days. Other "
        "laboratories' prices may differ.",
        "PJTAU pesticide residue laboratory, published rates",
        "2025-26",
        "provider",
        "https://pjtau.edu.in/pesticides-lab/",
    ),
    Finding(
        "FSSAI describes its DART household tests as an awareness tool. No accuracy data for them has been published.",
        "FSSAI DART booklet",
        "2017 onwards",
        "official",
        "https://www.fssai.gov.in/book-details.php?bkid=201",
    ),
)


def search_topics(q: str) -> list[Topic]:
    words = [w for w in (q or "").lower().split() if len(w) >= 3]
    if not words:
        return []
    return [t for t in TOPICS if any(w in f"{t.name} {t.also_called} {t.search_terms}".lower() for w in words)]
