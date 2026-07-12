"""acode command line.

    acode serve                          run the MCP server (stdio)
    acode import <file.json> [...]      import convention JSON files
    acode export                         dump all conventions as JSON
    acode list [--language L]            list conventions
    acode check <file> [--language L]    mechanical rule check
    acode search --language L [...]      hybrid RAG search (BM25+AST+metadata)
    acode index <path> [--language L]    index a codebase as patterns
    acode corpus build [...]             (re)build the corpus database
    acode corpus stats                   corpus composition and index stats
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .agent import steps
from .astcore.parser import language_for_path
from .config import AcodeConfig
from .rag.indexer import index_codebase
from .rag.store import ConventionStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="acode", description=__doc__)
    parser.add_argument("--db", help="convention database path (default: ACODE_DB)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("serve", help="run the MCP server on stdio")

    p = sub.add_parser("import", help="import convention JSON files")
    p.add_argument("files", nargs="+")
    p.add_argument("--replace", action="store_true")

    sub.add_parser("export", help="dump all conventions as JSON")

    p = sub.add_parser("list", help="list conventions")
    p.add_argument("--language")
    p.add_argument("--kind", choices=["rule", "pattern"])

    p = sub.add_parser("check", help="mechanically check a file")
    p.add_argument("file")
    p.add_argument("--language")

    p = sub.add_parser("search", help="hybrid search (BM25 + AST + metadata)")
    p.add_argument("--language", required=True)
    p.add_argument("--query", help="keyword/natural-language query (BM25)")
    p.add_argument("--code-file", help="rank by AST similarity to this file")
    p.add_argument("--framework")
    p.add_argument("--category")
    p.add_argument("--tag", action="append", dest="tags")
    p.add_argument("--top-k", type=int, default=8)

    p = sub.add_parser("corpus", help="corpus lifecycle")
    corpus_sub = p.add_subparsers(dest="corpus_command", required=True)
    pb = corpus_sub.add_parser("build", help="(re)build the corpus database")
    pb.add_argument("--conventions-dir", default="conventions",
                    help="directory of convention *.json files (default: conventions)")
    pb.add_argument("--index", action="append", dest="index_paths", default=[],
                    help="source path to index as patterns (repeatable)")
    pb.add_argument("--keep", action="store_true",
                    help="update the existing DB instead of rebuilding fresh")
    pb.add_argument("--max-files", type=int, default=500)
    corpus_sub.add_parser("stats", help="corpus composition and index stats")

    p = sub.add_parser("index", help="index a codebase as pattern conventions")
    p.add_argument("path")
    p.add_argument("--language")
    p.add_argument("--max-files", type=int, default=500)

    args = parser.parse_args(argv)
    config = AcodeConfig()
    if args.db:
        config.db_path = args.db

    if args.command == "serve":
        from .mcpserver.server import build_server

        build_server(config).run()
        return 0

    if args.command == "corpus" and args.corpus_command == "build":
        from .rag.corpus import build_corpus

        report = build_corpus(
            config.db_path,
            conventions_dir=args.conventions_dir,
            index_paths=args.index_paths,
            fresh=not args.keep,
            max_files=args.max_files,
        )
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        print()
        return 1 if report["errors"] else 0

    store = ConventionStore(config.db_path)

    if args.command == "corpus" and args.corpus_command == "stats":
        from .rag.corpus import corpus_stats

        json.dump(corpus_stats(store), sys.stdout, ensure_ascii=False, indent=2)
        print()
        return 0

    if args.command == "import":
        for path in args.files:
            added = store.import_file(path, replace=args.replace)
            print(f"{path}: imported {len(added)} convention(s): {', '.join(added)}")
        return 0

    if args.command == "export":
        json.dump(store.export_all(), sys.stdout, ensure_ascii=False, indent=2)
        print()
        return 0

    if args.command == "list":
        for conv in store.list(language=args.language, kind=args.kind):
            print(f"{conv.id}\t{conv.kind}\t{conv.language}\t{conv.title}")
        return 0

    if args.command == "check":
        language = args.language or language_for_path(args.file)
        if not language:
            print(f"cannot infer language for {args.file}; pass --language", file=sys.stderr)
            return 2
        code = Path(args.file).read_text(encoding="utf-8")
        rules = steps.applicable_rules(store, language, None)
        report = steps.check(code, language, rules)
        json.dump(report.to_dict(), sys.stdout, ensure_ascii=False, indent=2)
        print()
        return 0 if report.passed else 1

    if args.command == "search":
        metadata: dict = {}
        if args.framework:
            metadata["framework"] = args.framework
        if args.category:
            metadata["category"] = args.category
        if args.tags:
            metadata["tags"] = args.tags
        code = Path(args.code_file).read_text(encoding="utf-8") if args.code_file else None
        hits = store.search(language=args.language, metadata=metadata,
                            code=code, query=args.query, top_k=args.top_k)
        json.dump([h.to_dict() for h in hits], sys.stdout, ensure_ascii=False, indent=2)
        print()
        return 0

    if args.command == "index":
        result = index_codebase(store, args.path, language=args.language,
                                max_files=args.max_files)
        print(f"indexed {len(result['indexed'])} pattern(s) "
              f"from {result['files']} file(s), skipped {result['skipped']}")
        return 0

    parser.error(f"unknown command {args.command!r}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
