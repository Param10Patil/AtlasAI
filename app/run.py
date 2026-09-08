'''Exec-form container launcher that reads the configured port.'''

import argparse
import os

import uvicorn

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Run one OpsPilot logical service boundary')
    parser.add_argument('--role', default='api', choices=('api', 'triage', 'knowledge', 'resolution'))
    args = parser.parse_args()
    os.environ.setdefault('OPSPILOT_ROLE', args.role)
    uvicorn.run(
        'app.main:app',
        host='0.0.0.0',
        port=int(os.getenv('PORT', '8080')),
    )
