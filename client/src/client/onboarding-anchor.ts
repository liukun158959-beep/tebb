import { useEffect, useState } from 'react';
import type { GuideRect } from './onboarding-state';

export function guideAnchor(anchor: string): HTMLElement | null {
  return Array.from(document.querySelectorAll<HTMLElement>(`[data-guide-anchor="${anchor}"]`))
    .find(node => node.getClientRects().length > 0 && !node.closest('[hidden]')) || null;
}

// Follow the real field through mounting, scrolling, zooming and sidebar resizing.
export function useGuideAnchor(anchor: string) {
  const [rect, setRect] = useState<GuideRect | null>(null);
  useEffect(() => {
    let target: HTMLElement | null = null;
    let frame = 0;
    const size = new ResizeObserver(() => schedule());
    const measure = () => {
      frame = 0;
      const next = guideAnchor(anchor);
      if (next !== target) {
        if (target) size.unobserve(target);
        target = next;
        if (target) { target.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'auto' }); size.observe(target); }
      }
      const box = target?.getBoundingClientRect();
      const value = box ? { left: box.left, top: box.top, width: box.width, height: box.height } : null;
      setRect(old => JSON.stringify(old) === JSON.stringify(value) ? old : value);
    };
    function schedule() { if (!frame) frame = requestAnimationFrame(measure); }
    const mounted = new MutationObserver(schedule);
    mounted.observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['hidden', 'style', 'open'] });
    window.addEventListener('resize', schedule); window.addEventListener('scroll', schedule, true);
    window.visualViewport?.addEventListener('resize', schedule); window.visualViewport?.addEventListener('scroll', schedule);
    schedule();
    return () => {
      cancelAnimationFrame(frame); size.disconnect(); mounted.disconnect();
      window.removeEventListener('resize', schedule); window.removeEventListener('scroll', schedule, true);
      window.visualViewport?.removeEventListener('resize', schedule); window.visualViewport?.removeEventListener('scroll', schedule);
    };
  }, [anchor]);
  return rect;
}
