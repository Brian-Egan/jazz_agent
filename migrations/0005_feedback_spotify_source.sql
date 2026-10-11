-- 0005_feedback_spotify_source.sql -- comment-only (docs/DC.md section 11, ADR-022).
--
-- 0001's table comment says the MCP server is the only writer of feedback. Since DC the
-- pipeline writes rows with source = 'spotify' too, when Brian deletes a LaPortas track from
-- AlxJazz. That is not an MCP write; invariant 7 is unaffected.

COMMENT ON TABLE feedback IS
    'Append-only in practice. Written by the MCP server (source mcp), and by the pipeline for
     removal-derived dislikes (source spotify, DC section 11).';
COMMENT ON COLUMN feedback.source IS
    'mcp = stated over MCP. spotify = inferred from a track Brian deleted from an AlxJazz
     playlist, written by the pipeline. implicit is reserved for signal derived from
     recently-played rather than stated.';
