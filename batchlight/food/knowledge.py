"""What testing has actually found in common foods, with sources.

Rules for this content (see docs/FOOD_SAFETY.md):

* Every finding names its source, year and evidence type, and links to it.
* The evidence type describes the page we link to, not what that page talks about:
  "official" = published by a regulator, government body, Parliament or the WHO
  (notices, orders, statistics, guidance); "lab_test" = published by a government
  body or regulator and stating that a laboratory tested a specific sample or batch
  and it failed a legal standard (never used for aggregate statistics); "study" =
  a peer-reviewed paper; "report" = a news or NGO report, including news of an
  official order or test; "provider" = a service provider's information about
  itself, such as a laboratory's price list.
* Findings describe what was found in samples. They never claim that a brand,
  a pack or "most" of a food is adulterated, and never suggest a home test can
  prove adulteration.
"""

from __future__ import annotations

from dataclasses import dataclass, field

EVIDENCE_LABELS = {
    "official": "Official source",
    "lab_test": "Official lab result",
    "report": "News or NGO report",
    "study": "Peer-reviewed study",
    "provider": "Provider's own information",
}
EVIDENCE_HELP = {
    "official": "Published by a regulator, government body, Parliament or the WHO. The link goes to their own page.",
    "lab_test": "A regulator, government body or the WHO published this result: a laboratory tested a sample and it "
    "failed a quality or safety standard. It applies to the sample or batch tested.",
    "report": "From a news or NGO report, not from the regulator's or laboratory's own publication. "
    "Check the details in the linked report.",
    "study": "Research checked by other scientists before publication. It describes the samples tested, not every pack.",
    "provider": "What a service provider says about itself, such as a laboratory's prices.",
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
    "There is no reliable way to prove adulteration at home. FSSAI's DART booklet lists simple home checks, and FSSAI "
    "says its main aim is to create awareness. We have not found any published data on how accurate these checks are. "
    "Only a food-testing laboratory can confirm what is in a sample."
)

COMMON_ACTIONS = (
    "Prefer packaged food that shows the FSSAI logo with a licence or registration number, a batch or lot number, and a "
    'date mark such as "use by" or "expiry".',
    "Check the pack against recalls on this site, and keep the pack and receipt if something seems wrong.",
    "Report a suspicious product here and to FSSAI through the Food Safety Connect app.",
    "To check a sample for a specific problem, such as a named adulterant, pesticide residues or lead, have it tested at "
    "a laboratory accredited for that test. The result applies only to that sample and only to the tests that were run.",
)

# Lok Sabha unstarred question 1092 (24 July 2026), Ministry of Health and Family Welfare, with its annexures.
_LS_1092 = "https://sansad.in/getFile/lsapps/loksabhaquestions/annex/188/AU1092_7TaOa5.pdf"
_MILK_SURVEY_FINAL = "https://www.fssai.gov.in/upload/uploadfiles/files/Report_Milk_Survey_NMQS_Final_18_10_2019.pdf"
_TURMERIC_SUMMARY = "https://medicaldialogues.in/mdtv/medical-news-today/medical-bulletin-12-november-2024-137975"
_PATANJALI_REPORT = (
    "https://www.business-standard.com/india-news/"
    "patanjali-foods-recalls-4-tons-of-red-chilli-powder-over-pesticide-residue-125012400871_1.html"
)

TOPICS: tuple[Topic, ...] = (
    Topic(
        slug="overview-india",
        name="What India's food tests find",
        also_called="FSSAI surveillance results",
        summary=(
            "Food safety officers in India test well over a lakh food samples a year. In recent years about one in five "
            "samples did not meet a standard or rule. Official replies do not always say how many were unsafe."
        ),
        findings=(
            Finding(
                "In 2023-24, State and UT food safety authorities analysed 170,513 food samples. 33,808 of them (19.8%) "
                "did not meet a standard or rule. The reply does not say how many of these were unsafe and how many "
                "failed only on quality or labelling.",
                "Lok Sabha reply, unstarred question 1092, Annexure-III, Ministry of Health and Family Welfare",
                "2026",
                "official",
                _LS_1092,
            ),
            Finding(
                "In 2025-26 (provisional figures), State and UT food safety authorities analysed 223,808 food samples and "
                "found that 40,023 (about 17.9%) did not meet a standard or rule. The reply did not say how many were "
                "unsafe.",
                "Lok Sabha reply, unstarred question 1092, Annexure-I, Ministry of Health and Family Welfare",
                "2026",
                "official",
                _LS_1092,
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
            "India's national milk survey (2018) found that contamination from animal feed and veterinary drugs was more "
            "common than deliberate toxic adulteration. Paneer substitutes sold as paneer are a current enforcement issue."
        ),
        findings=(
            Finding(
                "In FSSAI's national milk survey of 6,432 samples, aflatoxin M1 was above the limit in 368 (5.7%) and "
                "antibiotic residues in 77 (1.2%). Only 12 samples contained adulterants that could affect safety, such as "
                "hydrogen peroxide, detergent, urea or neutralisers.",
                "FSSAI National Milk Safety and Quality Survey 2018, final report",
                "2019",
                "official",
                _MILK_SURVEY_FINAL,
            ),
            Finding(
                "FSSAI noted that aflatoxin M1 reaches milk through contaminated cattle feed and fodder.",
                "FSSAI National Milk Safety and Quality Survey 2018, final report",
                "2019",
                "official",
                _MILK_SURVEY_FINAL,
            ),
            Finding(
                "Maharashtra's Food and Drug Administration found that 109 of 308 paneer and dairy-substitute samples "
                "(35.4%) failed the standards between April 2025 and March 2026, including 30 judged unsafe.",
                "Maharashtra FDA, reported by the Free Press Journal",
                "2026",
                "report",
                "https://www.freepressjournal.in/amp/mumbai/maharashtra-govt-bans-analogue-paneer-for-one-year-after-354-per-cent-of-samples-fail-fda-quality-tests",
            ),
        ),
        concern=(
            "Aflatoxin M1 is a toxin from mould in animal feed. Antibiotic residues can help germs become resistant to "
            "medicines (antimicrobial resistance). Substitute paneer made with vegetable fat is not unsafe in itself, but "
            "selling it as paneer misleads buyers."
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
            "Results can differ from batch to batch, so a recall may cover only some batches."
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
                "In April 2024, Hong Kong's Centre for Food Safety said its routine tests found the pesticide ethylene "
                "oxide in samples of three MDH spice mixes (Madras Curry Powder, Sambhar Masala and Curry Powder) and "
                "Everest Fish Curry Masala bought from shops in Hong Kong. On its instructions, the importers and "
                "distributors recalled these products in Hong Kong.",
                "Centre for Food Safety, Hong Kong, press release",
                "2024",
                "lab_test",
                "https://www.cfs.gov.hk/english/press/20240405_10919.html",
            ),
            Finding(
                "Singapore's food agency also recalled Everest Fish Curry Masala, saying it contained ethylene oxide above "
                "the permitted level.",
                "Singapore Food Agency, reported by Scroll",
                "2024",
                "report",
                "https://scroll.in/latest/1066928/hong-kong-authorities-recall-ban-sale-of-four-everest-mdh-spice-products-imported-from-india",
            ),
            Finding(
                "FSSAI later took 34 samples from MDH and Everest factories in India (25 MDH, 9 Everest). The Health "
                "Ministry told Parliament in July 2024 that no ethylene oxide was found in any of them. This covers only "
                "those samples and only ethylene oxide.",
                "Ministry of Health and Family Welfare, reply in Lok Sabha, unstarred question 837, citing FSSAI",
                "2024",
                "official",
                "https://sansad.in/getFile/lsapps/loksabhaquestions/annex/182/AU837_QkaCSZ.pdf?source=lsapps",
            ),
            Finding(
                "FSSAI ordered Everest Food Products to recall one batch of pre-packed cumin powder (batch E080668761) "
                "after a food analyst, and then a referral food laboratory, found residues of the pesticides azoxystrobin "
                "and thiamethoxam above the prescribed maximum limits and declared the sample unsafe. The report does not "
                "say whether other batches were tested.",
                "FSSAI, reported by Business Today",
                "2026",
                "report",
                "https://www.businesstoday.in/india/story/unsafe-fssai-orders-recall-of-everest-cumin-powder-batch-after-pesticide-residues-found-above-limits-559413-2026-10-04",
            ),
        ),
        concern=(
            "IARC, the WHO's cancer research agency, classes ethylene oxide as a cause of cancer in humans. Pesticide "
            "residues above legal limits are not permitted in food sold in India."
        ),
        home_testing=HOME_TESTING_GENERAL,
        what_you_can_do=(
            "Check the batch number on your pack against the recalls on this site, and keep the pack and receipt if "
            "something seems wrong.",
            *COMMON_ACTIONS[2:],
        ),
        search_terms="spice masala chilli cumin jeera curry ethylene oxide pesticide mdh everest",
    ),
    Topic(
        slug="turmeric",
        name="Turmeric",
        also_called="haldi",
        summary=(
            "A peer-reviewed study found evidence that lead chromate, a bright yellow industrial pigment, had been added to "
            "turmeric in some markets. About 14% of samples had lead above 2 micrograms per gram, and some had extremely "
            "high levels."
        ),
        findings=(
            Finding(
                "Researchers tested 356 turmeric samples from 23 major cities in India, Pakistan, Sri Lanka and Nepal, "
                "collected in 2020-21. About 14% had lead above 2 micrograms per gram. Samples from Patna in Bihar, and "
                "from Karachi and Peshawar in Pakistan, had more than 1,000 micrograms per gram.",
                "Forsyth et al., Science of the Total Environment (abstract on Stanford University's website)",
                "2024",
                "study",
                "https://fsi.stanford.edu/node/36768",
            ),
            Finding(
                "The researchers concluded that the most likely source of the lead was lead chromate, a yellow pigment used "
                "in paints, plastics and ceramic coatings. Polished turmeric roots were the most contaminated, followed by "
                "loose powder, then packaged branded powder. Unpolished roots were the least contaminated. India's limit "
                "for lead in turmeric is 10 micrograms per gram.",
                "Forsyth et al., Science of the Total Environment (summary by Medical Dialogues)",
                "2024",
                "study",
                _TURMERIC_SUMMARY,
            ),
        ),
        concern=(
            "WHO says there is no level of lead exposure known to be without harmful effects. Young children are "
            "especially at risk, and lead can harm an unborn baby."
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
            "FSSAI's national edible oil survey (2020) found that about one in four samples failed quality tests, while a "
            "small share failed safety tests."
        ),
        findings=(
            Finding(
                "Of 4,461 edible oil samples, 108 (2.42%) failed safety tests for aflatoxins, pesticide residues or heavy "
                "metals, and 1,080 (24.2%) failed quality tests. 572 (12.8%) failed labelling rules, including fortified "
                "oils with less of the added vitamin A or D than required.",
                "FSSAI All-India Edible Oil Survey",
                "2020",
                "official",
                "https://www.fssai.gov.in/upload/uploadfiles/files/Report_Edible_Oil_Survey_27_01_2022.pdf",
            ),
        ),
        concern=(
            "Failing quality tests can mean the oil was mixed with cheaper oil. Safety failures involve contaminants that "
            "can harm health."
        ),
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
                "In 2020 the Centre for Science and Environment (CSE), an NGO, had 13 honey brands tested. Most big brands "
                "passed India's purity tests at an Indian laboratory. CSE said 10 of the 13 brands then failed an NMR test "
                "done by an unnamed laboratory in Germany. Some companies disputed the findings: one cited an NMR pass "
                "from a different German laboratory, and another said its honey met Indian (FSSAI) standards.",
                "Centre for Science and Environment, reported by Business Today",
                "2020",
                "report",
                "https://www.businesstoday.in/latest/corporate/story/honeygate-only-saffola-markfedsohna-natures-nectar-among-13-brands-pass-sugar-syrup-test-claims-cse-280275-2020-12-02",
            ),
            Finding(
                "Researchers tested 98 genuine honey samples, collected from beekeepers and government bee research "
                "centres in 17 states, against FSSAI's purity rules. 94% passed three isotope tests used to detect added "
                "sugar syrup. But 14% failed another of FSSAI's tests and 47% failed a further isotope test, so genuine "
                "honey can fail some of these tests. The researchers called for more study.",
                "Dua et al., Foods (peer-reviewed journal)",
                "2025",
                "study",
                "https://mdpi-res.com/d_attachment/foods/foods-14-01289/article_deploy/foods-14-01289.pdf",
            ),
        ),
        concern="Syrup-adulterated honey is mainly a fraud on buyers.",
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
        summary=(
            "State food safety departments have found banned dyes, or too much permitted colour, in popular street foods, "
            "and have banned using those colours in them."
        ),
        findings=(
            Finding(
                "In February 2024, a government food analysis laboratory found Rhodamine B, an industrial dye not allowed "
                "in food, in cotton candy samples from Chennai. Tamil Nadu's health minister then said it is illegal to "
                "make, pack, sell or serve food containing Rhodamine B.",
                "Tamil Nadu Health Department, reported by Business Today",
                "2024",
                "report",
                "https://www.businesstoday.in/india/story/cancer-scare-tamil-nadu-pondicherry-ban-cotton-candy-andhra-tests-samples-418276-2024-02-20",
            ),
            Finding(
                "In March 2024, Karnataka's health minister said 15 of 25 cotton candy samples and more than 100 of 171 "
                "gobi manchurian samples collected in the state were unsafe. Colours found included tartrazine, "
                "carmoisine, sunset yellow and Rhodamine B. The state's food safety commissioner then ordered a ban on "
                "artificial colours in these two foods.",
                "Karnataka Health Department, reported by Business Today",
                "2024",
                "report",
                "https://www.businesstoday.in/india/story/karnataka-bans-coloured-gobi-manchurian-cotton-candy-over-health-concerns-420941-2024-03-11",
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
        summary=(
            "Government figures, reported in the news, show pesticide residues above Indian limits in a small share of "
            "samples tested."
        ),
        findings=(
            Finding(
                "Of 86,401 food samples tested for pesticide residues in 2022-25, 2.8% were above FSSAI's legal limits for "
                "pesticide residues (maximum residue limits).",
                "Government statement to Parliament, reported by Medical Dialogues",
                "2025",
                "report",
                "https://health.medicaldialogues.in/health/fssai-finds-pesticide-residues-above-safe-limits-in-28-of-food-samples-153097",
            ),
            Finding(
                "In January 2025, FSSAI ordered Patanjali Foods to recall one batch of red chilli powder for not meeting "
                "India's rules on contaminants and residues. The company said the tested sample was over the limit for "
                "pesticide residues.",
                "FSSAI order disclosed by Patanjali Foods, reported by Business Standard (PTI)",
                "2025",
                "report",
                _PATANJALI_REPORT,
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
    "WHO estimates that unsafe food caused about 866 million illnesses and 1.5 million deaths worldwide in 2021. WHO's "
    "African and South-East Asia regions (the South-East Asia region includes India) together account for nearly "
    "three-quarters of these illnesses and 60% of the deaths.",
    "World Health Organization, news release on its 2026 foodborne disease estimates",
    "2026",
    "official",
    "https://www.who.int/news/item/04-06-2026-unsafe-food-causes-866-million-illnesses-and-1.5-million-deaths-annually--young-children-at-highest-risk",
)

TESTING_FACTS = (
    Finding(
        "As of July 2026, 88 rapid food testing kits (RAFT kits) had been approved, the Health Ministry told Parliament. "
        "These portable kits are for quick tests in the field. The ministry says they cut the time needed to screen food "
        "and so speed up checking and monitoring.",
        "Press Information Bureau, Ministry of Health and Family Welfare (reply in Lok Sabha)",
        "2026",
        "official",
        "https://www.pib.gov.in/PressReleasePage.aspx?PRID=2292341",
    ),
    Finding(
        "As of July 2026, 305 mobile food testing labs, called Food Safety on Wheels, are deployed across 35 states and "
        "union territories for food testing, training and awareness, especially in villages, towns and remote areas.",
        "Lok Sabha reply, unstarred question 1092, Ministry of Health and Family Welfare",
        "2026",
        "official",
        _LS_1092,
    ),
    Finding(
        "At the pesticide residue laboratory of Professor Jayashankar Telangana Agricultural University (PJTAU) in "
        "Hyderabad, testing one sample for pesticide residues by both of its methods is listed at Rs 14,000 for "
        "commercial customers (Rs 10,000 to Rs 12,000 for one method), with results in about 7 to 10 days. The lab mainly "
        "tests fresh farm produce, checks a fixed list of pesticides, and does not say whether it accepts samples from "
        "individual consumers. Other laboratories' prices may differ.",
        "PJTAU pesticide residue laboratory, published rates",
        "2026",
        "provider",
        "https://pjtau.edu.in/pesticides-lab/",
    ),
    Finding(
        "FSSAI's DART booklet lists simple household checks for some common adulterants. FSSAI says its main aim is to "
        "create awareness.",
        "FSSAI, DART booklet",
        "undated",
        "official",
        "https://www.fssai.gov.in/docs/knowledge-hub/books/DART-Book.pdf",
    ),
)


def search_topics(q: str) -> list[Topic]:
    words = [w for w in (q or "").lower().split() if len(w) >= 3]
    if not words:
        return []
    return [t for t in TOPICS if any(w in f"{t.name} {t.also_called} {t.search_terms}".lower() for w in words)]
