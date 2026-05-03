/**
 * Lightweight tooltip wrapper.
 *
 * On web we use the native `title` attribute (works with mouse hover and
 * with screen readers via name+description), no portals, no overlay layout.
 * On native we just render the children — there's no reliable
 * cross-platform hover surface.
 *
 * For richer accessibility, also consumers can pair this with
 * `accessibilityLabel` on the wrapped child.  This component does not break
 * focus or keyboard navigation.
 */

import React, { ReactElement, ReactNode, cloneElement, isValidElement } from "react";
import { Platform, View } from "react-native";

export interface TooltipProps {
  /**
   * The body of the tooltip. Kept short — multi-line tooltips on web are
   * inconsistent across browsers.
   */
  text: string;
  children: ReactNode;
  /** When true, render the title even on the wrapping View (helpful for
   * non-leaf children). */
  attachToWrapper?: boolean;
}

export function Tooltip({ text, children, attachToWrapper = false }: TooltipProps) {
  if (Platform.OS !== "web") return <>{children}</>;
  if (!text) return <>{children}</>;

  // If the child is a single element we can attach `title` to it directly so
  // the tooltip lives on the actual interactive element (good for screen
  // readers and good for keyboard focus).
  if (!attachToWrapper && isValidElement(children)) {
    const existing = (children as ReactElement<any>).props ?? {};
    return cloneElement(children as ReactElement<any>, {
      // @ts-ignore — RN-Web honors title on most components
      title: existing.title || text,
      "aria-label": existing.accessibilityLabel ?? existing["aria-label"] ?? undefined,
    });
  }

  return (
    <View
      // @ts-ignore — RN-Web honors title
      title={text}
    >
      {children}
    </View>
  );
}
