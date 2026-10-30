/** Navigate exactly in the order shown on the RPS stock list, skipping stocks
 * without a usable history file. Never reorder by code or current price. */
export function navigableStocks(stocks) {
  return Array.isArray(stocks) ? stocks.filter(stock => stock && stock.has_history === true) : [];
}
export function stockNavigation(stocks, currentCode) {
  const ordered = navigableStocks(stocks);
  const index = ordered.findIndex(stock => stock.code === currentCode);
  return {
    index,
    total: ordered.length,
    previous: index > 0 ? ordered[index - 1] : null,
    next: index >= 0 && index < ordered.length - 1 ? ordered[index + 1] : null,
  };
}
