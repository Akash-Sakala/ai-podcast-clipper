import { useDropzone } from "react-dropzone";
import { toast } from "sonner";

const MAX_BYTES = 4 * 1024 * 1024 * 1024;

/** Drag-and-drop (and the file picker) for one .mp4 of up to 4 GB. */
export function useVideoDropzone({
  onFile,
  disabled,
}: {
  onFile: (file: File) => void;
  disabled: boolean;
}) {
  return useDropzone({
    onDrop: (accepted) => {
      if (accepted[0]) onFile(accepted[0]);
    },
    onDropRejected: () =>
      toast.error("That file can't be used", {
        description: "Choose one .mp4 file up to 4 GB.",
      }),
    accept: { "video/mp4": [".mp4"] },
    maxSize: MAX_BYTES,
    maxFiles: 1,
    noClick: true,
    noKeyboard: true,
    disabled,
  });
}
