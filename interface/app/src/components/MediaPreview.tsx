import { motion } from "motion/react";
import { Download, Send } from "lucide-react";

// Windows paths to media files in an answer ("Done (text_overlay, GPU): D:\ISHTI\data\video_output\x.mp4").
const MEDIA = /[A-Za-z]:\\[^\n"'<>|?*`]+?\.(?:mp4|webm|mov|mkv|png|jpe?g|gif|webp|mp3|wav|m4a)\b/gi;
const kind = (p: string) => (/\.(mp4|webm|mov|mkv)$/i.test(p) ? "video" : /\.(mp3|wav|m4a)$/i.test(p) ? "audio" : "image");
const src = (p: string, download = false) => `/api/media?path=${encodeURIComponent(p)}${download ? "&download=1" : ""}`;

/** Plays or shows every media file an answer mentions, with Download and Send on WhatsApp. */
export function MediaPreview({ text, onSend }: { text: string; onSend: (path: string) => void }) {
  const paths = [...new Set(text.match(MEDIA) ?? [])].slice(0, 4);
  if (!paths.length) return null;
  return (
    <div className="mt-2 space-y-3">
      {paths.map((p, i) => {
        const name = p.split("\\").pop();
        return (
          <motion.figure key={p} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.05 }}
            className="max-w-xl overflow-hidden rounded-2xl border border-border bg-card/60">
            {kind(p) === "video" ? <video src={src(p)} controls preload="metadata" className="max-h-96 w-full bg-black" />
              : kind(p) === "audio" ? <audio src={src(p)} controls className="w-full p-2" />
              : <img src={src(p)} alt={name} loading="lazy" className="max-h-96 w-full object-contain bg-muted/40" />}
            <figcaption className="flex flex-wrap items-center gap-2 px-3 py-2 text-xs">
              <span className="min-w-0 flex-1 truncate text-muted-foreground" title={p}>{name}</span>
              <a href={src(p, true)} download className="flex items-center gap-1 rounded-lg border border-border px-2 py-1 hover:bg-muted">
                <Download className="h-3.5 w-3.5" /> Download</a>
              <button type="button" onClick={() => onSend(p)}
                className="flex items-center gap-1 rounded-lg bg-[#25D366] px-2 py-1 font-medium text-white hover:opacity-90 cursor-pointer">
                <Send className="h-3.5 w-3.5" /> Send on WhatsApp</button>
            </figcaption>
          </motion.figure>
        );
      })}
    </div>
  );
}
