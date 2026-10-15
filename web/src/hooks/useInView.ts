// useInView - becomes true the first time the element is on screen, and stays
// true. Used to defer a request until a row is actually visible. Where
// IntersectionObserver does not exist (old engines) it reports true at once,
// so the feature degrades to "fetch on mount" rather than never fetching.

import { useCallback, useEffect, useRef, useState } from 'react';

export function useInView<T extends Element>(): [(el: T | null) => void, boolean] {
  const supported = typeof IntersectionObserver !== 'undefined';
  const [seen, setSeen] = useState(!supported);
  const observer = useRef<IntersectionObserver | null>(null);

  const ref = useCallback(
    (el: T | null) => {
      observer.current?.disconnect();
      observer.current = null;
      if (!el || !supported) return;
      const io = new IntersectionObserver((entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setSeen(true);
          io.disconnect();
        }
      });
      io.observe(el);
      observer.current = io;
    },
    [supported],
  );

  useEffect(() => () => observer.current?.disconnect(), []);
  return [ref, seen];
}
