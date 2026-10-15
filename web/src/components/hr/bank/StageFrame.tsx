// StageFrame - the frame every stage of the bank flow sits in: a heading that
// takes focus when the stage opens, a one-line description, then the stage's
// content, which brings its own Card (the coding generator is a Card of its own,
// so the frame never adds one around it).
//
// Focus follows the stage. Choosing another stage replaces the panel under the
// person's cursor, so without this a keyboard or screen-reader user is left on a
// button that no longer controls anything. `focusOnMount` is false for the
// first paint of the page, so landing on a bank does not steal focus.
//
// Below `lg` the rail is an accordion whose open item already prints the stage
// title, so the heading is kept for assistive tech and focus but not drawn twice.

import { useEffect, useId, useRef, type ReactNode } from 'react';

interface StageFrameProps {
  title: string;
  description?: ReactNode;
  focusOnMount?: boolean;
  /** Controls at the right of the heading. */
  actions?: ReactNode;
  children: ReactNode;
  'data-testid'?: string;
}

export function StageFrame({
  title,
  description,
  focusOnMount = false,
  actions,
  children,
  'data-testid': testId,
}: StageFrameProps): JSX.Element {
  const id = useId();
  const heading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    if (focusOnMount) heading.current?.focus();
    // Only when the stage opens: this component is keyed by stage.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div data-testid={testId} className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 max-w-[640px]">
          <h2
            id={id}
            ref={heading}
            tabIndex={-1}
            className="sr-only text-[20px] font-bold tracking-[-0.3px] text-foreground focus-visible:outline-none lg:not-sr-only"
          >
            {title}
          </h2>
          {description ? (
            <p className="text-[14px] leading-relaxed text-muted-foreground lg:mt-1">
              {description}
            </p>
          ) : null}
        </div>
        {actions ? <div className="flex flex-none items-center gap-2">{actions}</div> : null}
      </div>
      {children}
    </div>
  );
}
