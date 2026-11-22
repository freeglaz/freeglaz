import { createContext, useContext } from 'react';

// Model family of the connected printer, from the live status (App.jsx):
// 'z9' | 'z9pro' | 'designjet' | null (unknown: unreachable, not yet loaded).
const PrinterFamilyContext = createContext(null);

export const PrinterFamilyProvider = PrinterFamilyContext.Provider;

export function usePrinterFamily() {
  return useContext(PrinterFamilyContext);
}
