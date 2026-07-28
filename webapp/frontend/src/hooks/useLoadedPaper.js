import { createContext, useContext } from 'react';

const LoadedPaperContext = createContext(null);

export const LoadedPaperProvider = LoadedPaperContext.Provider;

export function useLoadedPaper() {
  return useContext(LoadedPaperContext);
}
