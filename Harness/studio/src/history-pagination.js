const pageSize = 24;

export function historyPage(items, requestedPage = 1) {
  const total = items.length;
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const page = Math.min(pages, Math.max(1, Number.isInteger(requestedPage) ? requestedPage : 1));
  const offset = (page - 1) * pageSize;
  return {items: items.slice(offset, offset + pageSize), page, pages, total,
    from: total ? offset + 1 : 0, to: Math.min(offset + pageSize, total)};
}
