def format_money_thousand_separator(amount):
    """Format integer amounts with thousand separators using dots."""
    if amount is None:
        return "0"
    return f"{int(amount):,}".replace(",", ".")
