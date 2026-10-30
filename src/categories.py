"""Shared editorial categories, in Discord display order."""

CATEGORIES = ("AI", "テクノロジー", "経済・市場", "国際", "国内", "Web3・金融")


def normalize_category(category: str) -> str:
    return category if category in CATEGORIES else "テクノロジー"
