from apps.coach_web import taxonomy


def test_all_tags_reads_repo_taxonomy():
    tags = taxonomy.all_tags()
    assert "api-backend" in tags and "websockets-sse" in tags
    assert len(tags) >= 20
    assert tags == sorted(tags)
