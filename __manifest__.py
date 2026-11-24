# -*- coding: utf-8 -*-
{
    'name': 'Meeting Recorder (by ISD)',
    'version': '18.0.1.0.0',
    'category': 'ISD Modules',
    'summary': 'Record Discuss calls (video or audio only), keep them in a Meetings menu '
               'and let AI write the transcript, summary and action items',
    'description': """
Meeting Recorder (by ISD)
=========================
* A Record button in Discuss calls: video + sound, or sound only
* The first person in the call is the host and decides who may record:
  only the host, everyone, or the people the host picks
* Everyone in the call sees a red "Recording" badge
* Recordings are uploaded while the call goes on and kept in Meetings,
  with a player, download and delete
* One button asks AI (Gemini or OpenAI) for the transcript, summary,
  action items and decisions
    """,
    'author': 'IntelliSyncData',
    'website': 'https://intellisyncdata.com',
    'license': 'LGPL-3',
    'depends': ['mail', 'bus', 'web'],
    'external_dependencies': {'python': ['requests']},
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/cron_data.xml',
        'views/meeting_views.xml',
        'views/recording_views.xml',
        'views/ai_config_views.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'isd_meeting/static/src/**/*',
        ],
    },
    'images': ['static/description/icon.png'],
    'installable': True,
    'application': True,
}
