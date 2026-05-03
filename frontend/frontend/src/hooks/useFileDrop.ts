/**
 * Web-only window-wide drag-and-drop file hook.
 *
 * Returns isDragging (true while a file is hovering anywhere on the window)
 * and dispatches the dropped File to the supplied callback.  Handles the
 * dragenter/dragleave book-keeping so the highlight doesn't flicker as the
 * cursor moves over child elements.
 */

import { useEffect, useState } from "react";
import { Platform } from "react-native";

export function useFileDrop(onFile: (file: File) => void) {
  const [isDragging, setIsDragging] = useState(false);

  useEffect(() => {
    if (Platform.OS !== "web") return;
    if (typeof window === "undefined") return;

    let depth = 0;

    const onDragEnter = (e: DragEvent) => {
      if (!_hasFiles(e)) return;
      e.preventDefault();
      depth += 1;
      if (depth === 1) setIsDragging(true);
    };
    const onDragLeave = (e: DragEvent) => {
      if (!_hasFiles(e)) return;
      e.preventDefault();
      depth = Math.max(0, depth - 1);
      if (depth === 0) setIsDragging(false);
    };
    const onDragOver = (e: DragEvent) => {
      if (!_hasFiles(e)) return;
      e.preventDefault();
    };
    const onDrop = (e: DragEvent) => {
      if (!_hasFiles(e)) return;
      e.preventDefault();
      depth = 0;
      setIsDragging(false);
      const file = e.dataTransfer?.files?.[0];
      if (file) onFile(file);
    };

    window.addEventListener("dragenter", onDragEnter);
    window.addEventListener("dragleave", onDragLeave);
    window.addEventListener("dragover", onDragOver);
    window.addEventListener("drop", onDrop);
    return () => {
      window.removeEventListener("dragenter", onDragEnter);
      window.removeEventListener("dragleave", onDragLeave);
      window.removeEventListener("dragover", onDragOver);
      window.removeEventListener("drop", onDrop);
    };
  }, [onFile]);

  return { isDragging };
}

function _hasFiles(event: DragEvent): boolean {
  const types = event.dataTransfer?.types;
  if (!types) return false;
  for (let i = 0; i < types.length; i += 1) {
    if (types[i] === "Files") return true;
  }
  return false;
}
