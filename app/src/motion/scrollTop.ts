import { createContext, useContext, useEffect, useRef } from 'react';
import { ScrollView } from 'react-native';
import { useReducedMotion } from '../theme';
export const ScrollRequest = createContext(0);
export function useScrollTop(focused = true) {
  const ref = useRef<ScrollView>(null), request = useContext(ScrollRequest), last = useRef(request), reduced = useReducedMotion();
  useEffect(() => { if (request !== last.current && focused) ref.current?.scrollTo({ y: 0, animated: !reduced }); last.current = request; }, [request, focused, reduced]);
  return ref;
}
