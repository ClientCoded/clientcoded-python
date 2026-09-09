content = open('clientcoded/trace.py').read()

# Edit 1: Add test context to ClientCodedExporter.export payload
old1 = "                    'timestamp': time.time(),\n                    'extra': {"
new1 = "                    'timestamp': time.time(),\n                    'test_run_id': get_test_context().get('test_run_id'),\n                    'question_index': get_test_context().get('question_index'),\n                    'extra': {"
content = content.replace(old1, new1)
print('Edit 1 done')

# Edit 2: Add test context to _send_direct_trace payload
old2 = "        'timestamp': time.time()\n    }"
new2 = "        'timestamp': time.time(),\n        'test_run_id': get_test_context().get('test_run_id'),\n        'question_index': get_test_context().get('question_index'),\n    }"
content = content.replace(old2, new2)
print('Edit 2 done')

# Edit 3: Add import after contextvars
old3 = "_turn = contextvars.ContextVar('turn', default=None)"
new3 = "_turn = contextvars.ContextVar('turn', default=None)\n\nfrom .context import get_test_context"
content = content.replace(old3, new3)
print('Edit 3 done')

open('clientcoded/trace.py', 'w').write(content)
print('All edits applied')
