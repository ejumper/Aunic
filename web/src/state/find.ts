import { create } from "zustand";

export interface FindSlice {
  active: boolean;
  replaceMode: boolean;
  findText: string;
  replaceText: string;
  caseSensitive: boolean;
  matchCount: number;
  currentMatchIndex: number | null;
  open: (options?: {
    replaceMode?: boolean;
    findText?: string;
    replaceText?: string;
  }) => void;
  close: () => void;
  setFindText: (text: string) => void;
  setReplaceText: (text: string) => void;
  setReplaceMode: (replaceMode: boolean) => void;
  toggleCaseSensitive: () => void;
  syncMatches: (matchCount: number, currentMatchIndex: number | null) => void;
  reset: () => void;
}

const EMPTY_STATE = {
  active: false,
  replaceMode: false,
  findText: "",
  replaceText: "",
  caseSensitive: false,
  matchCount: 0,
  currentMatchIndex: null,
};

export const useFindStore = create<FindSlice>((set) => ({
  ...EMPTY_STATE,

  open(options = {}) {
    set((state) => ({
      active: true,
      replaceMode: options.replaceMode ?? state.replaceMode,
      findText: options.findText ?? state.findText,
      replaceText: options.replaceText ?? state.replaceText,
    }));
  },

  close() {
    set({ ...EMPTY_STATE });
  },

  setFindText(findText) {
    set({ findText });
  },

  setReplaceText(replaceText) {
    set({ replaceText });
  },

  setReplaceMode(replaceMode) {
    set({ replaceMode });
  },

  toggleCaseSensitive() {
    set((state) => ({ caseSensitive: !state.caseSensitive }));
  },

  syncMatches(matchCount, currentMatchIndex) {
    set({ matchCount, currentMatchIndex });
  },

  reset() {
    set({ ...EMPTY_STATE });
  },
}));
