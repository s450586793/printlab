export function canAllocatePrinter(data, option, currentRevision) {
  return data?.ready === true && typeof currentRevision === 'string' && data.revision === currentRevision
    && option?.fit === 'fits' && option.selection_available === true;
}

export function allocationNeedsReview(data, currentRevision) {
  const selected = data?.selected_printer_id;
  if (selected === null || selected === undefined || selected === '') return false;
  if (data.assignment_current === false) return true;
  return !(Array.isArray(data.options) && data.options.some(option => String(option.printer_id) === String(selected)
    && canAllocatePrinter(data, option, currentRevision)));
}
