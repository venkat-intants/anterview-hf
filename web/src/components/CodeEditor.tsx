// CodeEditor — a lightweight syntax-highlighted code editor for the coding round.
// react-simple-code-editor (a controlled textarea) + Prism highlighting. Far
// lighter than Monaco/CodeMirror and code-split into the coding pages so it never
// touches the main bundle. One Prism instance covers every language we run.

import Editor from 'react-simple-code-editor';
import { highlight, languages } from 'prismjs';
// Import order matters: a base grammar must load before grammars that extend it
// (cpp extends c; most extend clike).
import 'prismjs/components/prism-clike';
import 'prismjs/components/prism-c';
import 'prismjs/components/prism-cpp';
import 'prismjs/components/prism-python';
import 'prismjs/components/prism-javascript';
import 'prismjs/components/prism-typescript';
import 'prismjs/components/prism-java';
import 'prismjs/components/prism-go';
import 'prismjs/components/prism-csharp';
import 'prismjs/components/prism-ruby';
import 'prismjs/components/prism-rust';
import 'prismjs/themes/prism-tomorrow.css';

// Our language slug -> Prism grammar key.
const PRISM_LANG: Record<string, string> = {
  python: 'python',
  javascript: 'javascript',
  typescript: 'typescript',
  java: 'java',
  cpp: 'cpp',
  c: 'c',
  go: 'go',
  csharp: 'csharp',
  ruby: 'ruby',
  rust: 'rust',
};

interface CodeEditorProps {
  value: string;
  onChange?: (next: string) => void;
  language: string;
  readOnly?: boolean;
  minHeight?: number;
  placeholder?: string;
  /** Unique id for the underlying textarea — required when multiple editors mount. */
  textareaId?: string;
}

export default function CodeEditor({
  value,
  onChange,
  language,
  readOnly = false,
  minHeight = 320,
  placeholder,
  textareaId,
}: CodeEditorProps) {
  const key = PRISM_LANG[language] ?? 'clike';
  const grammar = languages[key] ?? languages.clike ?? languages.markup;

  return (
    <div
      className="overflow-auto rounded-[12px] border border-white/[0.1] bg-[#0b0c0e]"
      style={{ maxHeight: 560 }}
    >
      <Editor
        value={value}
        onValueChange={(next) => onChange?.(next)}
        highlight={(code) => highlight(code, grammar, key)}
        readOnly={readOnly}
        padding={14}
        placeholder={placeholder}
        textareaId={textareaId}
        className="min-h-full text-[13px] leading-[1.6] text-[#e6e6e6]"
        style={{
          fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
          minHeight,
        }}
      />
    </div>
  );
}
