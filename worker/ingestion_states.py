"""Content absence, platform access failure and extraction uncertainty are distinct."""
TERMINAL = {'partial_extraction','resolved','needs_review','failed','resolve_failed','fetch_blocked','fetch_not_found',
            'fetch_ok_no_content','extraction_empty','needs_source_info'}
MANUAL_RETRY = {'partial_extraction','failed','resolve_failed','needs_review','needs_source_info','extraction_empty','fetch_ok_no_content','no_content_found'}
def message(state, platform='source', retrying=True):
    name={'tiktok':'TikTok','instagram':'Instagram','youtube':'YouTube'}.get(platform,'The source platform')
    return {'resolve_failed':"Couldn't open this link",'fetch_blocked':f'{name} is limiting access — retrying automatically' if retrying else f'{name} is limiting access',
            'fetch_not_found':'This post was deleted or is private','fetch_ok_no_content':'This post has no caption, text or speech to read',
            'extraction_empty':"Couldn't find anything to save here",'needs_source_info':"We couldn't read this post. Add a venue or topic to save it.",
            'failed':'Processing was interrupted. You can retry.'}.get(state)
