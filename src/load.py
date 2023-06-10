# -*- coding: utf-8 -*-
# BioScan plugin for EDMC
# Source: https://github.com/Silarn/EDMC-BioScan
# Licensed under the [GNU Public License (GPL)](http://www.gnu.org/licenses/gpl-2.0.html) version 2 or later.
# Core imports
import concurrent.futures
from concurrent.futures import Future
from copy import deepcopy
from os import listdir, cpu_count
from os.path import expanduser, getctime
from pathlib import Path
from typing import Mapping, MutableMapping, Optional
from urllib.parse import quote
import sys
import threading
import re
import requests
import semantic_version
import math

# TKinter imports
import tkinter as tk
from tkinter import ttk

# Local imports
from bio_scan.journal_parse import parse_journal
from bio_scan.nebula_data.reference_stars import coordinates as nebula_coords
from bio_scan.nebula_data.sectors import planetary_nebulae, data as nebula_sectors
from bio_scan.status_flags import StatusFlags2, StatusFlags
from bio_scan.body_data.struct import PlanetData, StarData, load_planets, load_stars, get_main_star
from bio_scan.body_data.util import get_body_shorthand, body_check, get_gravity_warning, star_check
from bio_scan.body_data.edsm import parse_edsm_star_class, map_edsm_type, map_edsm_atmosphere
from bio_scan.bio_data.codex import parse_variant, set_codex, check_codex, check_codex_from_name
from bio_scan.bio_data.genus import data as bio_genus
from bio_scan.bio_data.regions import region_map, guardian_sectors
from bio_scan.bio_data.species import rules as bio_types
from bio_scan.format_util import Formatter

from sqlalchemy import Engine, create_engine, select, delete
from sqlalchemy.orm import Session, sessionmaker, scoped_session
from bio_scan.body_data.db import Base as DBBase, Commander, PlanetFlora, FloraScans, Waypoint, migrate, System

# EDMC imports
from config import config
from theme import theme
from EDMCLogging import get_plugin_logger
import myNotebook as nb
from ttkHyperlinkLabel import HyperlinkLabel

# 3rd Party
from bio_scan.RegionMap import findRegion
from bio_scan.RegionMapData import regions as galaxy_regions

JOURNAL_REGEX = re.compile(r'^Journal(Alpha|Beta)?\.[0-9]{2,4}(-)?[0-9]{2}(-)?[0-9]{2}(T)?[0-9]{2}[0-9]{2}[0-9]{2}'
                           r'\.[0-9]{2}\.log$')


class This:
    """Holds module globals."""

    def __init__(self):
        self.formatter = Formatter()

        self.VERSION = semantic_version.Version('2.5.4')
        self.NAME = 'BioScan'

        # Settings vars
        self.focus_setting: Optional[tk.StringVar] = None
        self.signal_setting: Optional[tk.StringVar] = None
        self.focus_breakdown: Optional[tk.BooleanVar] = None
        self.waypoints_enabled: Optional[tk.BooleanVar] = None
        self.debug_logging_enabled: Optional[tk.BooleanVar] = None

        # GUI Objects
        self.frame: Optional[tk.Frame] = None
        self.scroll_canvas: Optional[tk.Canvas] = None
        self.scrollbar: Optional[ttk.Scrollbar] = None
        self.scrollable_frame: Optional[ttk.Frame] = None
        self.label: Optional[tk.Label] = None
        self.values_label: Optional[tk.Label] = None
        self.total_label: Optional[tk.Label] = None
        self.edsm_button: Optional[tk.Label] = None
        self.edsm_failed: Optional[tk.Label] = None
        self.update_button: Optional[HyperlinkLabel] = None
        self.journal_label: Optional[tk.Label] = None

        # Plugin state data
        self.commander: Optional[Commander] = None
        self.planets: dict[str, PlanetData] = {}
        self.main_stars: dict[str, StarData] = {}
        self.planet_cache: dict[
            str, dict[str, tuple[bool, tuple[str, int, int, list[tuple[str, list[str], int]]]]]] = {}
        self.sql_engine: Optional[Engine] = None
        self.sql_session_factory: Optional[scoped_session] = None
        self.sql_session: Optional[Session] = None
        self.migration_failed: bool = False
        self.journal_thread: Optional[threading.Thread] = None
        self.parsing_journals: bool = False
        self.journal_stop: bool = False
        self.journal_event: Optional[threading.Event] = None
        self.journal_progress: float = 0.0
        self.journal_error: bool = False

        # self.odyssey: bool = False
        # self.game_version: semantic_version.Version = semantic_version.Version.coerce('0.0.0.0')
        self.main_star_type: str = ''
        self.main_star_luminosity: str = ''
        self.location_name: str = ''
        self.location_id: str = ''
        self.location_state: str = ''
        self.planet_radius: float = 0.0
        self.planet_latitude: Optional[float] = None
        self.planet_longitude: Optional[float] = None
        self.planet_altitude: float = 10000.0
        self.planet_heading: Optional[int] = None
        self.current_scan: str = ''
        self.system: Optional[System] = None

        # EDSM vars
        self.edsm_thread: Optional[threading.Thread] = None
        self.edsm_session: Optional[str] = None
        self.edsm_bodies: Optional[Mapping] = None
        self.fetched_edsm = False


this = This()
logger = get_plugin_logger(this.NAME)


def plugin_start3(plugin_dir: str) -> str:
    """ EDMC start hook """

    engine_path = config.app_dir_path / 'bioscan.db'
    this.sql_engine = create_engine(f'sqlite:///{engine_path}', connect_args={'timeout': 30})
    DBBase.metadata.create_all(this.sql_engine)
    result = migrate(this.sql_engine)
    if not result:
        this.migration_failed = True
    this.sql_session_factory = scoped_session(sessionmaker(bind=this.sql_engine))
    this.sql_session = Session(this.sql_engine)
    return this.NAME


def plugin_app(parent: tk.Frame) -> tk.Frame:
    """ EDMC initialization """

    this.frame = tk.Frame(parent)
    this.frame.grid_columnconfigure(0, weight=1)
    if this.migration_failed:
        this.label = tk.Label(this.frame, text='BioScan: DB Migration Failed')
        this.label.grid(row=0, sticky=tk.EW)
        this.update_button = HyperlinkLabel(this.frame, text='Please Check or Submit an Issue',
                                            url='https://github.com/Silarn/EDMC-BioScan/issues')
        this.update_button.grid(row=1, columnspan=2, sticky=tk.N)
    else:
        parse_config()
        this.frame.bind('<<BioScanEDSMData>>', edsm_data)
        this.frame.bind('<<bioscan_journal_start>>', journal_start)
        this.frame.bind('<<bioscan_journal_progress>>', journal_update)
        this.frame.bind('<<bioscan_journal_finish>>', journal_end)
        this.label = tk.Label(this.frame)
        this.label.grid(row=0, column=0, columnspan=2, sticky=tk.N)
        this.scroll_canvas = tk.Canvas(this.frame, height=80, highlightthickness=0)
        this.scrollbar = ttk.Scrollbar(this.frame, orient='vertical', command=this.scroll_canvas.yview)
        this.scrollable_frame = ttk.Frame(this.scroll_canvas)
        this.scrollable_frame.bind(
            '<Configure>',
            lambda e: this.scroll_canvas.configure(
                scrollregion=this.scroll_canvas.bbox('all')
            )
        )
        this.scroll_canvas.bind('<Enter>', bind_mousewheel)
        this.scroll_canvas.bind('<Leave>', unbind_mousewheel)
        this.scroll_canvas.create_window((0, 0), window=this.scrollable_frame, anchor='nw')
        this.scroll_canvas.configure(yscrollcommand=this.scrollbar.set)
        this.values_label = ttk.Label(this.scrollable_frame)
        this.values_label.pack(fill='both', side='left')
        this.scroll_canvas.grid(row=1, column=0, sticky=tk.EW)
        this.scroll_canvas.grid_rowconfigure(1, weight=0)
        this.scrollbar.grid(row=1, column=1, sticky=tk.NSEW)
        this.total_label = tk.Label(this.frame)
        this.total_label.grid(row=2, column=0, columnspan=2, sticky=tk.N)
        this.edsm_button = tk.Label(this.frame, text='Fetch EDSM Data', fg='white', cursor='hand2')
        this.edsm_button.grid(row=3, columnspan=2, sticky=tk.EW)
        this.edsm_button.bind('<Button-1>', lambda e: edsm_fetch())
        this.edsm_failed = tk.Label(this.frame, text='No EDSM Data Found')
        this.journal_label = tk.Label(this.frame, text='Journal Parsing')
        update = version_check()
        if update != '':
            text = f'Version {update} is now available'
            url = f'https://github.com/Silarn/EDMC-BioScan/releases/tag/v{update}'
            this.update_button = HyperlinkLabel(this.frame, text=text, url=url)
            this.update_button.grid(row=4, columnspan=2, sticky=tk.N)
        update_display()
        theme.register(this.values_label)
    return this.frame


def plugin_prefs(parent: ttk.Notebook, cmdr: str, is_beta: bool) -> tk.Frame:
    """ EDMC settings pane hook """

    x_padding = 10
    x_button_padding = 12
    y_padding = 2
    frame = nb.Frame(parent)
    frame.columnconfigure(0, weight=1)
    frame.columnconfigure(1, weight=1)
    frame.rowconfigure(20, weight=1)

    HyperlinkLabel(frame, text=this.NAME, background=nb.Label().cget('background'),
                   url='https://github.com/Silarn/EDMC-BioScan', underline=True) \
        .grid(row=1, padx=x_padding, sticky=tk.W)
    nb.Label(frame, text='Version %s' % this.VERSION).grid(row=1, column=1, padx=x_padding, sticky=tk.E)

    ttk.Separator(frame).grid(row=5, columnspan=2, pady=y_padding * 2, sticky=tk.EW)

    nb.Label(
        frame,
        text='Focus Body Signals:',
    ).grid(row=10, padx=x_padding, sticky=tk.W)
    focus_options = [
        'Never',
        'On Approach',
        'Near Surface',
        'On Surface',
    ]
    nb.OptionMenu(
        frame,
        this.focus_setting,
        this.focus_setting.get(),
        *focus_options
    ).grid(row=11, padx=x_padding, pady=y_padding, column=0, sticky=tk.W)
    nb.Label(frame,
             text='Never: Never filter signal details\n' +
                  'On Approach: Show only local signals on approach\n' +
                  'Near Surface: Show signals under 5km altitude\n' +
                  'On Surface: Show only local signals when on surface',
             justify=tk.LEFT) \
        .grid(row=12, padx=x_padding, column=0, sticky=tk.NW)
    nb.Checkbutton(
        frame,
        text='Show complete breakdown of genera with multiple matches',
        variable=this.focus_breakdown
    ).grid(row=13, column=0, padx=x_button_padding, sticky=tk.W)

    nb.Label(
        frame,
        text='Display Signal Summary:'
    ).grid(row=10, column=1, sticky=tk.W)
    signal_options = [
        'Always',
        'In Flight',
    ]
    nb.OptionMenu(
        frame,
        this.signal_setting,
        this.signal_setting.get(),
        *signal_options
    ).grid(row=11, column=1, pady=y_padding, sticky=tk.W)
    nb.Label(frame,
             text='Always: Always display the body signal summary\n' +
                  'In Flight: Show the signal summary in flight only',
             justify=tk.LEFT) \
        .grid(row=12, column=1, sticky=tk.NW)
    nb.Checkbutton(
        frame,
        text='Enable species waypoints with the comp. scanner',
        variable=this.waypoints_enabled
    ).grid(row=13, column=1, sticky=tk.W)

    nb.Button(frame, text='Start / Stop Parse Journals', command=parse_journals) \
        .grid(row=20, column=0, padx=x_padding, sticky=tk.SW)

    nb.Checkbutton(
        frame,
        text='Enable Debug Logging',
        variable=this.debug_logging_enabled
    ).grid(row=20, column=1, padx=x_button_padding, sticky=tk.SE)
    return frame


def prefs_changed(cmdr: str, is_beta: bool) -> None:
    """ EDMC settings changed hook """

    config.set('bioscan_focus', this.focus_setting.get())
    config.set('bioscan_focus_breakdown', this.focus_breakdown.get())
    config.set('bioscan_signal', this.signal_setting.get())
    config.set('bioscan_waypoints', this.waypoints_enabled.get())
    config.set('bioscan_debugging', this.debug_logging_enabled.get())
    update_display()


def parse_config() -> None:
    """ Load saved settings vars """

    this.focus_setting = tk.StringVar(value=config.get_str(key='bioscan_focus', default='On Approach'))
    this.focus_breakdown = tk.BooleanVar(value=config.get_bool(key='bioscan_focus_breakdown', default=False))
    this.signal_setting = tk.StringVar(value=config.get_str(key='bioscan_signal', default='Always'))
    this.waypoints_enabled = tk.BooleanVar(value=config.get_bool(key='bioscan_waypoints', default=True))
    this.debug_logging_enabled = tk.BooleanVar(value=config.get_bool(key='bioscan_debugging', default=False))


def version_check() -> str:
    """
    Parse latest GitHub release version
    :return: The latest version string if it's newer than ours
    """

    try:
        req = requests.get(url='https://api.github.com/repos/Silarn/EDMC-BioScan/releases/latest')
        data = req.json()
        if req.status_code != requests.codes.ok:
            raise requests.RequestException
    except (requests.RequestException, requests.JSONDecodeError) as ex:
        logger.error('Failed to parse GitHub release info', exc_info=ex)
        return ''

    version = semantic_version.Version(data['tag_name'][1:])
    if version > this.VERSION:
        return str(version)
    return ''


def plugin_stop() -> None:
    if this.journal_thread and this.journal_thread.is_alive():
        this.journal_stop = True
        if this.journal_event:
            this.journal_event.set()
    try:
        this.sql_session_factory.close()
        this.sql_session.commit()
        this.sql_session.close()
        this.sql_engine.dispose()
    except Exception as ex:
        logger.error('Error during cleanup commit', exc_info=ex)


def log(*args) -> None:
    """
    Debug logger helper function. Only writes to log if debug logging is enabled for BioScan.
    :param args: Arguments to be passed to the EDMC logger
    """

    if this.debug_logging_enabled.get():
        logger.debug(args)


def parse_journals() -> None:
    if not this.parsing_journals:
        if not this.journal_thread or not this.journal_thread.is_alive():
            this.journal_thread = threading.Thread(target=journal_worker, name='Journal worker')
            this.journal_thread.daemon = True
            this.journal_thread.start()
    else:
        this.journal_stop = True
        if this.journal_event:
            this.journal_event.set()


def journal_worker() -> None:
    journal_dir = config.get_str('journaldir')
    journal_dir = journal_dir if journal_dir else config.default_journal_dir

    journal_dir = expanduser(journal_dir)

    if journal_dir == '':
        return

    this.parsing_journals = True
    this.journal_error = False
    this.frame.event_generate('<<bioscan_journal_start>>')

    try:
        journal_files: list[Path] = [Path(journal_dir) / str(x) for x in listdir(journal_dir) if
                                     JOURNAL_REGEX.search(x)]

        if journal_files:
            journal_files = sorted(journal_files, key=getctime)
            count = 0
            this.journal_event = threading.Event()
            with concurrent.futures.ThreadPoolExecutor(max_workers=min([cpu_count(), 4])) as executor:
                future_journal: dict[Future, Path] = {executor.submit(parse_journal, journal,
                                                                      this.sql_session_factory, this.journal_event):
                                                      journal for journal in journal_files}
                for future in concurrent.futures.as_completed(future_journal):
                    count += 1
                    this.journal_progress = count / len(journal_files)
                    this.frame.event_generate('<<bioscan_journal_progress>>')
                    if not future.result() or this.journal_stop:
                        if not future.result():
                            this.journal_error = True
                        this.parsing_journals = False
                        this.journal_event.set()
                        executor.shutdown(wait=True, cancel_futures=True)
                        break

    except Exception as ex:
        logger.error('Journal parsing failed', exc_info=ex)

    this.parsing_journals = False
    this.journal_stop = False
    this.journal_event = None
    this.frame.event_generate('<<bioscan_journal_finish>>')


def journal_start(event: tk.Event) -> None:
    this.journal_label.grid(row=5, columnspan=2, sticky=tk.EW)
    this.journal_label['text'] = 'Parsing Journals: 0%'


def journal_update(event: tk.Event) -> None:
    progress = f'{this.journal_progress:.1%}'
    progress = progress.rstrip('0').rstrip('.')
    this.journal_label['text'] = f'Parsing Journals: {progress}'
    update_display()


def journal_end(event: tk.Event) -> None:
    if this.journal_error:
        this.journal_label['text'] = 'Error During Journal Parse\nPlease Submit a Report'
    else:
        this.journal_label.grid_remove()
    update_display()


def edsm_fetch() -> None:
    """ EDSM system data fetch thread initialization """

    if not this.edsm_thread or not this.edsm_thread.is_alive():
        this.edsm_thread = threading.Thread(target=edsm_worker, name='EDSM worker', args=(this.system.name,))
        this.edsm_thread.daemon = True
        this.edsm_thread.start()


def edsm_worker(system_name: str) -> None:
    """ Fetch system data from EDSM on a threaded function """

    if not this.edsm_session:
        this.edsm_session = requests.Session()

    try:
        r = this.edsm_session.get('https://www.edsm.net/api-system-v1/bodies?systemName=%s' % quote(system_name),
                                  timeout=10)
        r.raise_for_status()
        this.edsm_bodies = r.json() or {}
    except requests.exceptions.RequestException:
        this.edsm_bodies = None

    this.frame.event_generate('<<BioScanEDSMData>>', when='tail')


def edsm_data(event: tk.Event) -> None:
    """ Handle data retrieved from EDSM """

    if this.edsm_bodies is None:
        return

    if len(this.edsm_bodies.get('bodies', [])) == 0:
        this.edsm_failed.grid(row=3, columnspan=2, sticky=tk.EW)

    for body in this.edsm_bodies.get('bodies', []):
        body_short_name = get_body_name(body['name'])
        if body['type'] == 'Star':
            if body['isMainStar']:
                if body['spectralClass']:
                    this.main_star_type = body['spectralClass'][:-1]
                else:
                    this.main_star_type = parse_edsm_star_class(body['subType'])
                this.main_star_luminosity = body['luminosity']

            if body_short_name == body['name']:
                add_edsm_star(body)
            else:
                if re.search('^[A-Z]$', body_short_name):
                    add_edsm_star(body)

        elif body['type'] == 'Planet':
            try:
                if body_short_name not in this.planets:
                    planet_data = PlanetData.from_journal(this.system, body_short_name,
                                                          body['bodyId'], this.sql_session)
                else:
                    planet_data = this.planets[body_short_name]
                planet_type = map_edsm_type(body['subType'])
                planet_data.set_type(planet_type) \
                    .set_distance(body['distanceToArrival']) \
                    .set_atmosphere(map_edsm_atmosphere(body['atmosphereType'])) \
                    .set_gravity(body['gravity'] * 9.80665) \
                    .set_temp(body['surfaceTemperature'])
                if body['volcanismType'] == 'No volcanism':
                    volcanism = ''
                else:
                    volcanism = body['volcanismType'].lower().capitalize() + ' volcanism'
                planet_data.set_volcanism(volcanism)

                star_search = re.search('^([A-Z]+) .+$', body_short_name)
                if star_search:
                    for star in star_search.group(1):
                        planet_data.add_parent_star(star)
                else:
                    planet_data.add_parent_star(this.system.name)

                if 'materials' in body:
                    for material in body['materials']:  # type: str
                        planet_data.add_material(material.lower())

                atmosphere_composition: dict[str, float] = body.get('atmosphereComposition', {})
                for gas, percent in atmosphere_composition.items():
                    planet_data.add_gas(map_edsm_atmosphere(gas), percent)

                this.planets[body_short_name] = planet_data

            except Exception as e:
                logger.error('Error while parsing EDSM', exc_info=e)
    this.fetched_edsm = True
    reset_cache()
    update_display()


def add_edsm_star(body: dict) -> None:
    """
    Add a parent star from EDSM API data

    :param body: The EDSM body data (JSON)
    """
    try:
        body_short_name = get_body_name(body['name'])
        if body_short_name not in this.main_stars:
            star_data = StarData.from_journal(this.system, body_short_name, body['bodyId'], this.sql_session)
        else:
            star_data = this.main_stars[body_short_name]
        if body['spectralClass']:
            star_data.set_type(body['spectralClass'][:-1])
        else:
            star_data.set_type(parse_edsm_star_class(body['subType']))
        star_data.set_luminosity(body['luminosity'])
        star_data.set_distance(body['distanceToArrival'])
        this.main_stars[body_short_name] = star_data
    except Exception as e:
        logger.error('Error while parsing EDSM', exc_info=e)


def scan_label(scans: int) -> str:
    """ Return the label for the scan stage """

    match scans:
        case 0:
            return 'Located'
        case 1:
            return 'Logged'
        case 2:
            return 'Sampled'
        case 3:
            return 'Analysed'


def value_estimate(body: PlanetData, genus: str) -> tuple[str, int, int, list[tuple[str, list[str], int]]]:
    """
    Main function to make species determinations from body data.
    Returns the display name and the minimum and maximum values.
    Data is cached, and we check a flag to see if we need to recalculate the species.

    :param body: The planet data we're fetching species for
    :param genus: The genus we're checking for species requirements
    :return: The display string for the calculated genus/species, the minimum and maximum values, and a
             list of individual species if there are multiple matches
    """

    if body.get_name() in this.planet_cache:
        if genus in this.planet_cache[body.get_name()] and not this.planet_cache[body.get_name()][genus][0]:
            return this.planet_cache[body.get_name()][genus][1]
    else:
        this.planet_cache[body.get_name()] = {}

    if genus not in this.planet_cache[body.get_name()]:
        this.planet_cache[body.get_name()][genus] = (True, ('', 0, 0, []))

    possible_species: dict[str, set[str]] = {}
    log(f'System: {this.system.name} - Body: {body.get_name()}')
    log(f'Running checks for {bio_genus[genus]["name"]}:')
    for species, data in bio_types[genus].items():
        log(species)
        count = 0
        for ruleset in data['rulesets']:
            count += 1
            log(f'Ruleset {count}')
            eliminated = False
            for rule_type, value in ruleset.items():
                stop = False
                match rule_type:
                    case 'atmosphere':
                        if value == 'Any' and body.get_atmosphere() in ['', 'None']:
                            log('Eliminated for no atmos')
                            eliminated = True
                            stop = True
                        elif value != 'Any' and body.get_atmosphere() not in value:
                            log(f'Eliminated for atmos ({body.get_atmosphere()} in {value})')
                            eliminated = True
                            stop = True
                    case 'atmosphere_component':
                        for gas, percent in value.items():
                            if body.get_gas(gas) < percent:
                                log('Eliminated for lack of gas in atmosphere')
                                eliminated = True
                                stop = True
                    case 'max_gravity':
                        if body.get_gravity() / 9.80665 > value:
                            log('Eliminated for high grav')
                            eliminated = True
                            stop = True
                    case 'min_gravity':
                        if body.get_gravity() / 9.80665 < value:
                            log('Eliminated for low grav')
                            eliminated = True
                            stop = True
                    case 'max_temperature':
                        if not body.get_temp():
                            continue
                        if body.get_temp() >= value:
                            log('Eliminated for high heat')
                            eliminated = True
                            stop = True
                    case 'min_temperature':
                        if not body.get_temp():
                            continue
                        if body.get_temp() < value:
                            log('Eliminated for low heat')
                            eliminated = True
                            stop = True
                    case 'volcanism':
                        log(f'Compare {value} to {body.get_volcanism()}')
                        if value == 'Any' and body.get_volcanism() == '':
                            log('Eliminated for no volcanism')
                            eliminated = True
                            stop = True
                        elif value == 'None' and body.get_volcanism() != '':
                            log('Eliminated for any volcanism')
                            eliminated = True
                            stop = True
                        elif isinstance(value, list):
                            found = False
                            for volc_type in value:
                                if body.get_volcanism().find(volc_type) != -1:
                                    found = True
                            if not found:
                                log('Eliminated for missing volcanism')
                                eliminated = True
                                stop = True
                    case 'body_type':
                        if body.get_type() not in value:
                            log('Eliminated for body type')
                            eliminated = True
                            stop = True
                    case 'regions':
                        if this.system.region is not None:
                            log(f'Current region: {this.system.region} - {galaxy_regions[this.system.region]}')
                            if this.system.region is not None:
                                for region in value:
                                    if region.startswith('!'):
                                        log(f'Not in region ({region[1:]}) map: {region_map[region[1:]]}')
                                        if this.system.region in region_map[region[1:]]:
                                            log('Eliminated by region')
                                            eliminated = True
                                            stop = True
                                            break

                                if not stop:
                                    found = False
                                    count = 0
                                    for region in value:
                                        if not region.startswith('!'):
                                            count += 1
                                            log(f'In region ({region}): {region_map[region]}')
                                            if this.system.region in region_map[region]:
                                                found = True

                                    if not found and count > 0:
                                        log('Eliminated by region')
                                        eliminated = True
                                        stop = True

                    case 'guardian':
                        found = False
                        for sector in guardian_sectors:
                            if this.system.name.startswith(sector):
                                found = True
                                stop = True
                        if not found:
                            log('Eliminated for not being in a guardian sector')
                            eliminated = True
                            stop = True
                    case 'life':
                        if not body_check(this.planets):
                            log('Eliminated for missing body type(s)')
                            eliminated = True
                            stop = True
                    case 'life_plus':
                        if not body_check(this.planets, True):
                            log('Eliminated for missing body type(s)')
                            eliminated = True
                            stop = True
                    case 'main_star':
                        if isinstance(value, list):
                            if isinstance(value[0], tuple):
                                match = False
                                for star_info in value:
                                    if star_check(star_info[0], this.main_star_type):
                                        for flag in ['', 'a', 'b', 'ab']:
                                            if star_info[1] + flag == this.main_star_luminosity:
                                                match = True
                                                break
                                        if match:
                                            break
                                if not match:
                                    log('Eliminated for star type')
                                    eliminated = True
                                    stop = True
                            else:
                                match = False
                                for star_type in value:
                                    if star_check(star_type, this.main_star_type):
                                        match = True
                                        break
                                if not match:
                                    log('Eliminated for star type')
                                    eliminated = True
                                    stop = True
                        else:
                            if not star_check(value, this.main_star_type):
                                log('Eliminated for star type')
                                eliminated = True
                                stop = True
                    case 'nebula':
                        if not this.system.x:
                            log('Missing system coordinates')
                            continue
                        found = False
                        if this.system.name in planetary_nebulae:
                            found = True
                        for sector in nebula_sectors:
                            if this.system.name.startswith(sector):
                                found = True
                                stop = True
                        for system, coords in nebula_coords.items():
                            distance = math.sqrt((coords[0] - this.system.x) ** 2
                                                 + (coords[1] - this.system.y) ** 2
                                                 + (coords[2] - this.system.z) ** 2)
                            log(f'Distance to {system} from {this.system.name}: {distance:n} ly')
                            if distance < 100.0:
                                found = True
                                stop = True
                        if not found:
                            log('Eliminated for lack of nebula')
                            eliminated = True
                            stop = True
                    case 'distance':
                        if body.get_distance() < value:
                            eliminated = True
                            stop = True
                if stop:
                    break
            if not eliminated:
                possible_species[species] = set()

    eliminated_species: set[str] = set()
    if 'colors' in bio_genus[genus]:
        if 'species' in bio_genus[genus]['colors']:
            for species in possible_species:
                if 'star' in bio_genus[genus]['colors']['species'][species]:
                    try:
                        for star in sorted(body.get_parent_stars(),
                                           key=lambda item: this.main_stars[item].get_id()):
                            for star_type in bio_genus[genus]['colors']['species'][species]['star']:
                                if star_check(star_type, this.main_stars[star].get_type()):
                                    possible_species[species].add(
                                        bio_genus[genus]['colors']['species'][species]['star'][star_type])
                                    break
                            if possible_species[species]:
                                break
                        if possible_species[species]:
                            continue
                        for star_name, star_data in sorted(this.main_stars.items(), key=lambda item: item[1].get_id()):
                            if star_name in body.get_parent_stars():
                                continue
                            for star_type in bio_genus[genus]['colors']['species'][species]['star']:
                                if star_check(star_type, star_data.get_type()):
                                    possible_species[species].add(
                                        bio_genus[genus]['colors']['species'][species]['star'][star_type])
                                    break
                            if possible_species[species]:
                                break
                    except KeyError:
                        log('Parent star not in main stars')
                elif 'element' in bio_genus[genus]['colors']['species'][species]:
                    for element in bio_genus[genus]['colors']['species'][species]['element']:
                        if element in body.get_materials():
                            possible_species[species].add(
                                bio_genus[genus]['colors']['species'][species]['element'][element])

                if not possible_species[species]:
                    eliminated_species.add(species)
                    log('Eliminated for lack of color')
        else:
            found_color = ''
            try:
                for star in sorted(body.get_parent_stars(), key=lambda item: this.main_stars[item].get_id()):
                    for star_type in bio_genus[genus]['colors']['star']:
                        log('Checking star type %s against %s' % (star_type, this.main_stars[star].get_type()))
                        if star_check(star_type, this.main_stars[star].get_type()):
                            found_color = bio_genus[genus]['colors']['star'][star_type]
                            break
                    if found_color:
                        break
                if not found_color:
                    for star_name, star_data in sorted(this.main_stars.items(), key=lambda item: item[1].get_id()):
                        if star_name in body.get_parent_stars():
                            continue
                        for star_type in bio_genus[genus]['colors']['star']:
                            if star_check(star_type, star_data.get_type()):
                                found_color = bio_genus[genus]['colors']['star'][star_type]
                                break
                        if found_color:
                            break
            except KeyError:
                log('Parent star not in main stars')
            if not found_color:
                possible_species.clear()
                log('Eliminated genus for lack of color')
            else:
                for species in possible_species:
                    possible_species[species].add(found_color)

    final_species: dict[str, list[str]] = {}
    for species in possible_species:
        if species not in eliminated_species:
            final_species[species] = sorted(possible_species[species])

    sorted_species: list[tuple[str, list[str]]] = sorted(
        final_species.items(),
        key=lambda target_species: bio_types[genus][target_species[0]]['value']
    )

    if len(sorted_species) == 1:
        localized_species: list[tuple[str, list[str], int]] = []
        codex = False
        if sorted_species[0][1]:
            for color in sorted_species[0][1]:
                if not check_codex(this.sql_session_factory, this.commander.id, this.system.region,
                                   genus, sorted_species[0][0], color):
                    codex = True
                    break
        else:
            codex = not check_codex(this.sql_session_factory, this.commander.id, this.system.region, genus,
                                    sorted_species[0][0])
        if len(sorted_species[0][1]) > 1:
            localized_species = [
                (bio_types[genus][sorted_species[0][0]]['name'],
                 sorted_species[0][1],
                 bio_types[genus][sorted_species[0][0]]['value'])
            ]
        this.planet_cache[body.get_name()][genus] = (
            False,
            (
                '{}{}{}'.format(
                    '\N{memo} ' if codex else '',
                    bio_types[genus][sorted_species[0][0]]['name'],
                    f' - {sorted_species[0][1][0]}' if len(sorted_species[0][1]) == 1 else ''
                ),
                bio_types[genus][sorted_species[0][0]]['value'], bio_types[genus][sorted_species[0][0]]['value'],
                localized_species
            )
        )
    elif len(sorted_species) > 0:
        color = ''
        codex = False
        localized_species = [
            (bio_types[genus][info[0]]['name'], info[1], bio_types[genus][info[0]]['value']) for info in sorted_species
        ]
        for species, colors in sorted_species:
            if not codex:
                if colors:
                    for color in colors:
                        if not check_codex(this.sql_session_factory, this.commander.id, this.system.region, genus,
                                           species, color):
                            codex = True
                            break
                else:
                    codex = not check_codex(this.sql_session_factory, this.commander.id, this.system.region,
                                            genus, species)
            if len(colors) > 1:
                color = ''
                break
            if len(colors) == 1:
                if color and colors[0] != color:
                    color = ''
                    break
                if not color:
                    color = colors[0]
        this.planet_cache[body.get_name()][genus] = (
            False,
            (
                '{}{}{}'.format(
                    '\N{memo} ' if codex else '',
                    bio_genus[genus]['name'],
                    f' - {color}' if color else ''
                ),
                bio_types[genus][sorted_species[0][0]]['value'],
                bio_types[genus][sorted_species[-1][0]]['value'],
                localized_species
            )
        )

    if this.planet_cache[body.get_name()][genus][0]:
        this.planet_cache[body.get_name()][genus] = (False, ('', 0, 0, []))

    return this.planet_cache[body.get_name()][genus][1]


def reset_cache(planet: str = '') -> None:
    """
    Resets the species calculation cache. If genus is passed, resets only that genus.

    :param planet: Optional parameter to reset only a specific genus
    """

    if planet and planet in this.planet_cache:
        for genus in this.planet_cache[planet]:
            this.planet_cache[planet][genus] = (True, this.planet_cache[planet][genus][1])
    else:
        for planet, data in this.planet_cache.items():
            for genus in data:
                data[genus] = (True, data[genus][1])


def get_possible_values(body: PlanetData) -> list[tuple[str, tuple[int, int, list[tuple[str, list[str], int]]]]]:
    """
    For unmapped planets, run through every genus and make species determinations

    :param body: The planet we're fetching
    :return: A dictionary of genera mapped to the minimum and maximum values and a list of species if there
             were multiple matches
    """

    possible_genus: list[tuple[str, tuple[int, int, list]]] = []
    for genus in sorted(bio_types, key=lambda item: bio_genus[item]['name']):
        name, min_potential_value, max_potential_value, all_species = value_estimate(body, genus)
        if min_potential_value != 0:
            possible_genus.append((name, (min_potential_value, max_potential_value, all_species)))

    return possible_genus


def get_body_name(fullname: str = '') -> str:
    """
    Remove the base system name from the body name if the body has a unique identifier.
    Usually only the main star has the same name as the system in one-star systems.

    :param fullname: The full name of the body including the system name
    :return: The short name of the body unless it matches the system name
    """

    if fullname.startswith(this.system.name + ' '):
        body_name = fullname[len(this.system.name + ' '):]
    else:
        body_name = fullname
    return body_name


def reset() -> None:
    """
    Reset system data when location changes
    """

    this.main_star_type = ''
    this.main_star_luminosity = ''
    this.location_name = ''
    this.location_id = -1
    this.location_state = ''
    this.fetched_edsm = False
    this.planets = {}
    this.planet_cache = {}
    this.main_stars = {}
    this.scroll_canvas.yview_moveto(0.0)
    this.sql_session.commit()


def add_star(entry: Mapping[str, any]):
    """
    Add main star data from journal event

    :param entry: The journal event dict (must be a Scan event with star data)
    """

    body_short_name = get_body_name(entry['BodyName'])

    if body_short_name not in this.main_stars:
        star_data = StarData.from_journal(this.system, body_short_name, entry['BodyID'], this.sql_session)
    else:
        star_data = this.main_stars[body_short_name]

    star_data.set_type(entry['StarType'])
    star_data.set_luminosity(entry['Luminosity'])
    star_data.set_distance(entry['DistanceFromArrivalLS'])

    this.main_stars[body_short_name] = star_data


def journal_entry(
        cmdr: str, is_beta: bool, system: str, station: str, entry: Mapping[str, any], state: MutableMapping[str, any]
) -> str:
    """ EDMC journal entry hook. Primary journal data handler. """

    if this.migration_failed:
        return ''

    system_changed = False
    # this.game_version = semantic_version.Version.coerce(state.get('GameVersion', '0.0.0'))
    # this.odyssey = state.get('Odyssey', False)
    if not state['StarPos']:
        return ''
    if system and (not this.system or system != this.system.name):
        reset()
        system_changed = True
        this.system = this.sql_session.scalar(select(System).where(System.name == system))
        if not this.system:
            this.system = System(name=system)
            this.sql_session.add(this.system)
            this.system.x = state['StarPos'][0]
            this.system.y = state['StarPos'][1]
            this.system.z = state['StarPos'][2]
            sector = findRegion(this.system.x, this.system.y, this.system.z)
            this.system.region = sector[0] if sector is not None else None
        this.planets = load_planets(this.system, this.sql_session)
        this.main_stars = load_stars(this.system, this.sql_session)
        main_star = get_main_star(this.system, this.sql_session)
        if main_star:
            this.main_star_type = main_star.type
            this.main_star_luminosity = main_star.luminosity

    if cmdr and not this.commander:
        stmt = select(Commander).where(Commander.name == cmdr)
        result = this.sql_session.scalars(stmt)
        this.commander = result.first()
        if not this.commander:
            this.commander = Commander(name=cmdr)
            this.sql_session.add(this.commander)
            this.sql_session.commit()

    log(f'Event {entry["event"]}')
    if entry['event'] == 'Scan':
        body_short_name = get_body_name(entry['BodyName'])
        if 'StarType' in entry:
            if entry['DistanceFromArrivalLS'] == 0.0:
                this.main_star_type = entry['StarType']
                this.main_star_luminosity = entry['Luminosity']

            if body_short_name == entry['BodyName']:
                add_star(entry)
            else:
                if re.search('^[A-Z]$', body_short_name):
                    add_star(entry)

            reset_cache()
            update_display()

        if 'PlanetClass' in entry:
            if body_short_name not in this.planets:
                body_data = PlanetData.from_journal(this.system, body_short_name,
                                                    entry['BodyID'], this.sql_session)
            else:
                body_data = this.planets[body_short_name]
            body_data.set_distance(float(entry['DistanceFromArrivalLS'])).set_type(entry['PlanetClass']) \
                .set_gravity(entry['SurfaceGravity']).set_temp(entry.get('SurfaceTemperature', 0)) \
                .set_volcanism(entry.get('Volcanism', ''))

            star_search = re.search('^([A-Z]+) .+$', body_short_name)
            if star_search:
                for star in star_search.group(1):
                    body_data.add_parent_star(star)
            else:
                body_data.add_parent_star(this.system.name)

            if 'Materials' in entry:
                for material in entry['Materials']:
                    body_data.add_material(material['Name'])

            if 'AtmosphereType' in entry:
                body_data.set_atmosphere(entry['AtmosphereType'])

            if 'AtmosphereComposition' in entry:
                for gas in entry['AtmosphereComposition']:
                    body_data.add_gas(gas['Name'], gas['Percent'])

            this.planets[body_short_name] = body_data

            reset_cache()
            update_display()

    elif entry['event'] in ['FSSBodySignals', 'SAASignalsFound']:
        body_short_name = get_body_name(entry['BodyName'])

        if body_short_name not in this.planets:
            body_data = PlanetData.from_journal(this.system, body_short_name, entry['BodyID'], this.sql_session)
        else:
            body_data = this.planets[body_short_name]

        # Add bio signal number just in case
        for signal in entry['Signals']:
            if signal['Type'] == '$SAA_SignalType_Biological;':
                body_data.set_bio_signals(signal['Count'])

        # If signals include genuses, add them to the body data
        if 'Genuses' in entry:
            for genus in entry['Genuses']:
                if body_data.get_flora(genus['Genus']) is None:
                    body_data.add_flora(genus['Genus'])

        this.planets[body_short_name] = body_data

        reset_cache(body_short_name)
        update_display()

    elif entry['event'] == 'ScanOrganic':
        target_body = None
        for name, body in this.planets.items():
            if body.get_id() == entry['Body']:
                target_body = name
                break

        scan_level = 0
        match entry['ScanType']:
            case 'Log':
                scan_level = 1
            case 'Sample':
                scan_level = 2
            case 'Analyse':
                scan_level = 3

        if target_body is not None:
            this.planets[target_body].set_flora_species_scan(
                entry['Genus'], entry['Species'], scan_level, this.commander.id
            )
            if this.current_scan != '' and this.current_scan != entry['Genus']:
                data: PlanetFlora = this.planets[target_body].get_flora(this.current_scan)
                if data:
                    this.planets[target_body].set_flora_species_scan(
                        this.current_scan, data.species, 0, this.commander.id
                    )
                    session = this.sql_session_factory()
                    stmt = delete(Waypoint).where(Waypoint.commander_id == this.commander.id) \
                        .where(Waypoint.flora_id == data.id) \
                        .where(Waypoint.type == 'scan')
                    session.execute(stmt)
                    session.commit()
            this.current_scan = entry['Genus']

            if 'Variant' in entry:
                _, _, color = parse_variant(entry['Variant'])
                this.planets[target_body].set_flora_color(entry['Genus'], color)

            match scan_level:
                case 1 | 2:
                    if this.planet_latitude and this.planet_longitude:
                        this.planets[target_body].add_flora_waypoint(
                            entry['Genus'],
                            (this.planet_latitude, this.planet_longitude),
                            this.commander.id,
                            scan=True
                        )
                case _:
                    this.current_scan = ''

        update_display()

    elif entry['event'] == 'CodexEntry' and entry['Category'] == '$Codex_Category_Biology;' and 'BodyID' in entry:
        target_body = None
        for name, body in this.planets.items():
            if body.get_id() == entry['BodyID']:
                target_body = name
                break

        if target_body is not None:
            genus, species, color = parse_variant(entry['Name'])
            if genus is not '' and species is not '':
                this.planets[target_body].add_flora(genus, species, color)
                if this.location_id == entry['BodyID'] and this.planet_latitude and this.planet_longitude:
                    this.planets[target_body].add_flora_waypoint(
                        genus, (this.planet_latitude, this.planet_longitude), this.commander.id
                    )

            set_codex(this.sql_session_factory, this.commander.id, entry['Name'], this.system.region)
            reset_cache()  # Required to clear found codex marks

        update_display()

    elif entry['event'] in ['ApproachBody', 'Touchdown', 'Liftoff']:
        if entry['event'] in ['Liftoff', 'Touchdown'] and entry['PlayerControlled'] is False:
            return ''
        body_name = get_body_name(entry['Body'])
        if body_name in this.planets:
            this.location_name = body_name
            this.location_id = entry['BodyID']

        if entry['event'] in ['ApproachBody', 'Liftoff']:
            this.location_state = 'approach'
        else:
            this.location_state = 'surface'

        update_display()

        # if this.focus_setting.get() == 'On Approach' and entry['event'] == 'ApproachBody':
        #     this.scroll_canvas.yview_moveto(0.0)
        #
        # if this.focus_setting.get() == 'On Surface' and entry['event'] in ['Touchdown', 'Liftoff']:
        #     this.scroll_canvas.yview_moveto(0.0)

    elif entry['event'] == 'LeaveBody':
        this.location_name = ''
        this.location_id = -1
        this.location_state = ''

        update_display()
        this.scroll_canvas.yview_moveto(0.0)

    if system_changed:
        update_display()

    return ''  # No error


def dashboard_entry(cmdr: str, is_beta: bool, entry: dict[str, any]) -> str:
    """ EDMC dashboard entry hook. Parses updates to the Status.json. """

    if this.migration_failed:
        return ''

    if 'BodyName' in entry:
        body_name = get_body_name(entry['BodyName'])
        if this.location_name == '' and body_name != this.system.name:
            this.location_name = body_name
        if this.location_id == -1 and body_name in this.planets:
            this.location_id = this.planets[body_name].get_id()

    status = StatusFlags(entry['Flags'])
    status2 = StatusFlags2(0)
    if 'Flags2' in entry:
        status2 = StatusFlags2(entry['Flags2'])
    refresh = False
    scroll = False

    current_state = this.location_state
    this.location_state = ''
    if StatusFlags.HAVE_LATLONG in status:
        if StatusFlags.IN_SHIP in status:
            if StatusFlags.LANDED in status:
                this.location_state = 'surface'
            else:
                this.location_state = 'approach'
        elif StatusFlags.IN_SRV in status or StatusFlags.LANDED in status:
            this.location_state = 'surface'
        elif StatusFlags2.ON_FOOT in status2 and StatusFlags2.PLANET_ON_FOOT in status2 \
                and StatusFlags2.SOCIAL_ON_FOOT not in status2 and StatusFlags2.STATION_ON_FOOT not in status2:
            this.location_state = 'surface'

    if current_state != this.location_state:
        if (this.focus_setting.get() == 'On Approach' and this.location_state == 'approach') or \
                (this.focus_setting.get() == 'On Surface' and this.location_state == 'surface') or \
                (this.focus_setting.get() != 'Never' and this.location_state == ''):
            scroll = True
        refresh = True

    if StatusFlags.HAVE_LATLONG in status:
        if 'Altitude' in entry:
            if this.focus_setting.get() == 'Near Surface' and \
                    (this.planet_altitude > 5000.0 > entry['Altitude'] or
                     this.planet_altitude < 5000.0 < entry['Altitude']):
                scroll = True
                refresh = True
            this.planet_altitude = entry['Altitude']
        else:
            this.planet_altitude = 0
        this.planet_latitude = entry['Latitude']
        this.planet_longitude = entry['Longitude']
        this.planet_radius = entry['PlanetRadius']
        this.planet_heading = entry['Heading'] if 'Heading' in entry else None
        try:
            if this.location_name != '' and (this.current_scan != ''
                                             or this.planets[this.location_name].has_waypoint(this.commander.id)):
                refresh = True
        except KeyError:
            log(f"Current location ({this.location_name}) has no planet data")
    else:
        this.planet_latitude = None
        this.planet_longitude = None
        this.planet_heading = None
        this.planet_altitude = 0 if this.location_state == 'surface' else 10000
        this.planet_radius = 0

    if refresh:
        update_display()
    if scroll:
        this.scroll_canvas.yview_moveto(0.0)

    return ''


def calc_bearing(lat_long: tuple[float, float]) -> float:
    """
    Get the bearing angle from your current position to the target position using lat/long coordinates.

    :param lat_long: The target lat/long coordinates.
    :return: The bearing angle (from 0-359)
    """
    lat_long2 = (this.planet_latitude, this.planet_longitude)
    phi_1 = math.radians(lat_long2[0])
    phi_2 = math.radians(lat_long[0])
    delta_lambda = math.radians(lat_long[1] - lat_long2[1])
    y = math.sin(delta_lambda) * math.cos(phi_2)
    x = math.cos(phi_1) * math.sin(phi_2) \
        - math.sin(phi_1) * math.cos(phi_2) * math.cos(delta_lambda)
    theta = math.atan2(y, x)
    return (math.degrees(theta) + 360) % 360


def calc_distance(lat_long: tuple[float, float], lat_long2: tuple[float, float] | None = None) -> float:
    """
    Use the haversine formula to get the distance between two points of latitude/longitude.

    :param lat_long: The lat/long coordinates of the first (target) position.
    :param lat_long2: Optional. The lat/long coordinates of the second position. Defaults to the current position.
    :return: The calculated distance.
    """
    lat_long2 = lat_long2 if lat_long2 else (this.planet_latitude, this.planet_longitude)
    phi_1 = math.radians(lat_long2[0])
    phi_2 = math.radians(lat_long[0])
    delta_phi = math.radians(lat_long[0] - lat_long2[0])
    delta_lambda = math.radians(lat_long[1] - lat_long2[1])
    a = math.sin(delta_phi / 2.0) ** 2 + \
        math.cos(phi_1) * math.cos(phi_2) * \
        math.sin(delta_lambda / 2.0) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return this.planet_radius * c


def get_distance(lat_long: tuple[float, float] | None = None) -> float | None:
    """
    Get the shortest distance to a scan location for the currently-in-progress species.

    :param lat_long: The lat/long coordinates to consider for the distance. Defaults to the current location.
    :return: The minimum distance or None if we don't have active scan info.
    """

    distance_list = []
    if this.planet_latitude is not None and this.planet_longitude is not None:
        if this.location_name and this.current_scan:
            waypoints: list[Waypoint] = this.planets[this.location_name].get_flora(this.current_scan).waypoints
            waypoints = list(
                filter(lambda item: item.type == 'scan' and item.commander_id == this.commander.id, waypoints))
            for waypoint in waypoints:
                distance_list.append(calc_distance((waypoint.latitude, waypoint.longitude), lat_long))
            return min(distance_list)
    return None


def get_nearest(genus: str, waypoints: list[Waypoint]) -> str:
    """
    Check logged waypoints and return the nearest one that's not within a previous sample radius.

    :param genus: The genus ID
    :param waypoints: The list of logged waypoints as lat/long
    :return: String with the distance and bearing to the nearest qualifying waypoint. If none is found, return an
             empty string.
    """

    if this.planet_heading and this.planet_latitude and this.planet_longitude:
        distances: list[tuple[float, float]] = []
        for waypoint in waypoints:
            min_distance = get_distance((waypoint.latitude, waypoint.longitude))
            if not min_distance or min_distance > bio_genus[genus]['distance']:
                distance = calc_distance((waypoint.latitude, waypoint.longitude))
                bearing = calc_bearing((waypoint.latitude, waypoint.longitude))
                distances.append((distance, bearing))

        if len(distances):
            distance, bearing = sorted(distances, key=lambda item: item[0])[0]
            distance_formatted = this.formatter.format_distance(int(distance), 'm', False)
            bearing_diff = abs(bearing - this.planet_heading) % 360
            bearing_diff = 360 - bearing_diff if bearing_diff > 180 else bearing_diff
            bearing_diff = bearing_diff if (this.planet_heading + bearing_diff) % 360 == bearing else bearing_diff * -1
            return '{}° ({}{}°), {}'.format(int(bearing),
                                            '-> ' if bearing_diff >= 0 else '<- ',
                                            int(abs(bearing_diff)),
                                            distance_formatted)

    return ''


def get_bodies_summary(bodies: dict[str, PlanetData], focused: bool = False) -> tuple[str, int]:
    """ Get body genus estimate display text for the scroll pane """

    detail_text = ''
    value_sum = 0
    for name, body in bodies.items():
        if not focused:
            detail_text += f'{name}:\n'
        if len(body.get_flora()) > 0:
            count = 0
            for flora in sorted(body.get_flora(), key=lambda item: bio_genus[item.genus]['name']):
                count += 1
                genus: str = flora.genus
                species: str = flora.species
                scan: list[FloraScans] = list(filter(lambda item: item.commander_id == this.commander.id, flora.scans))
                color: str = flora.color
                waypoints: list[Waypoint] = list(
                    filter(
                        lambda item: item.commander_id == this.commander.id and item.type == 'tag',
                        flora.waypoints
                    )
                )
                if scan and scan[0].count == 3:
                    value_sum += bio_types[genus][species]['value']
                if species != '':
                    waypoint = get_nearest(genus, waypoints) if (this.waypoints_enabled.get() and focused
                                                                 and this.current_scan == '' and waypoints) else ''
                    detail_text += '{}{}{} ({}): {}{}{}\n'.format(
                        '\N{memo} ' if not check_codex(this.sql_session_factory, this.commander.id,
                                                       this.system.region, genus, species, color) else '',
                        bio_types[genus][species]['name'],
                        f' - {color}' if color else '',
                        scan_label(scan[0].count if scan else 0),
                        this.formatter.format_credits(bio_types[genus][species]['value']),
                        u' 🗸' if scan == 3 else '',
                        f'\n  Nearest Saved Waypoint: {waypoint}' if waypoint else ''
                    )
                else:
                    bio_name, min_val, max_val, all_species = value_estimate(body, genus)
                    detail_text += '{} (Not located): {}\n'.format(
                        bio_name,
                        this.formatter.format_credit_range(min_val, max_val))
                    if this.focus_breakdown.get():
                        for species_details in all_species:
                            species_details_final = deepcopy(species_details)
                            if species_details_final[1] and len(species_details_final[1]) > 1:
                                for variant in species_details_final[1]:
                                    if not check_codex_from_name(this.sql_session_factory, this.commander.id,
                                                                 this.system.region, species_details_final[0], variant):
                                        species_details_final[1][species_details_final[1].index(variant)] = f'\N{memo}{variant}'
                            else:
                                variant = ''
                                if species_details_final[1]:
                                    variant = species_details_final[1][0]
                                if not check_codex_from_name(this.sql_session_factory, this.commander.id,
                                                             this.system.region, species_details_final[0], variant):
                                    species_details_final = (
                                        f'\N{memo}{species_details_final[0]}',
                                        species_details_final[1],
                                        species_details_final[2]
                                    )
                            detail_text += '  {}{}: {}\n'.format(
                                species_details_final[0],
                                ' - {}'.format(', '.join(species_details_final[1])) if species_details_final[1] else '',
                                this.formatter.format_credits(species_details_final[2])
                            )
                if len(body.get_flora()) == count:
                    detail_text += '\n'

        else:
            types = get_possible_values(body)
            detail_text += f'{body.get_bio_signals()} Signals - Possible Types:\n'
            count = 0
            for bio_name, values in types:
                count += 1
                detail_text += '{}: {}\n'.format(
                    bio_name,
                    this.formatter.format_credit_range(values[0], values[1])
                )
                if this.focus_breakdown.get():
                    for species_details in values[2]:
                        species_details_final = deepcopy(species_details)
                        if species_details_final[1] and len(species_details_final[1]) > 1:
                            for variant in species_details_final[1]:
                                if not check_codex_from_name(this.sql_session_factory, this.commander.id,
                                                             this.system.region, species_details_final[0], variant):
                                    species_details_final[1][species_details_final[1].index(variant)] = f'\N{memo}{variant}'
                        else:
                            variant = ''
                            if species_details_final[1]:
                                variant = species_details_final[1][0]
                            if not check_codex_from_name(this.sql_session_factory, this.commander.id,
                                                         this.system.region, species_details_final[0], variant):
                                species_details_final = (
                                    f'\N{memo}{species_details_final[0]}',
                                    species_details_final[1],
                                    species_details_final[2]
                                )
                        detail_text += '  {}{}: {}\n'.format(
                            species_details_final[0],
                            ' - {}'.format(', '.join(species_details_final[1])) if species_details_final[1] else '',
                            this.formatter.format_credits(species_details_final[2])
                        )
                if len(types) == count:
                    detail_text += '\n'

    return detail_text, value_sum


def update_display() -> None:
    """ Primary display update function. This is run whenever we get an event that would change the display state. """

    if this.fetched_edsm or not this.system:
        this.edsm_button.grid_remove()
    else:
        this.edsm_button.grid()
        this.edsm_failed.grid_remove()

    bio_bodies = dict(
        sorted(
            dict(
                filter(
                    lambda item: int(item[1].get_bio_signals()) if item[1].get_bio_signals() else 0 > 0 or len(
                        item[1].get_flora()) > 0,
                    this.planets.items()
                )
            ).items(),
            key=lambda item: item[1].get_id()
        )
    )
    exobio_body_names = [
        '{}{}{}: {}'.format(
            body_name,
            get_body_shorthand(body_data.get_type()),
            get_gravity_warning(body_data.get_gravity()),
            body_data.get_bio_signals()
        )
        for body_name, body_data
        in bio_bodies.items()
    ]

    if (this.location_name != '' and this.location_name in bio_bodies) and this.focus_setting.get() != 'Never' and \
            ((this.focus_setting.get() == 'On Approach' and this.location_state in ['approach', 'surface'])
             or (this.focus_setting.get() == 'On Surface' and this.location_state == 'surface')
             or (this.focus_setting.get() == 'Near Surface' and this.location_state in ['approach', 'surface']
                 and this.planet_altitude < 5000.0)):
        detail_text, total_value = get_bodies_summary({this.location_name: this.planets[this.location_name]}, True)
    else:
        detail_text, total_value = get_bodies_summary(bio_bodies)

    if len(bio_bodies) > 0:
        this.scroll_canvas.grid()
        this.scrollbar.grid()
        this.total_label.grid()
        text = 'BioScan Estimates:\n'

        if this.signal_setting.get() == 'Always' or this.location_state != 'surface':
            while True:
                exo_list = exobio_body_names[:5]
                exobio_body_names = exobio_body_names[5:]
                text += ' ⬦ '.join([b for b in exo_list])
                if len(exobio_body_names) == 0:
                    break
                else:
                    text += '\n'

        if (this.location_name != '' and this.location_name in bio_bodies) and this.focus_setting.get() != 'Never' and \
                ((this.focus_setting.get() == 'On Approach' and this.location_state in ['approach', 'surface'])
                 or (this.focus_setting.get() == 'On Surface' and this.location_state == 'surface')
                 or (this.focus_setting.get() == 'Near Surface' and this.location_state in ['approach', 'surface']
                     and this.planet_altitude < 5000.0)):
            if text[-1] != '\n':
                text += '\n'
            complete = 0
            floras = bio_bodies[this.location_name].get_flora()
            for flora in floras:
                for scan in filter(lambda item: item.commander_id == this.commander.id,
                                   flora.scans):  # type: FloraScans
                    if scan.count == 3:
                        complete += 1
            text += '{} - {} [{}G] - {}/{} Analysed'.format(
                bio_bodies[this.location_name].get_name(),
                bio_bodies[this.location_name].get_type(),
                '{:.2f}'.format(bio_bodies[this.location_name].get_gravity() / 9.80665).rstrip('0').rstrip('.'),
                complete, len(bio_bodies[this.location_name].get_flora())
            )
            for flora in this.planets[this.location_name].get_flora():
                genus: str = flora.genus
                species: str = flora.species
                scan_list: list[FloraScans] = list(
                    filter(lambda item: item.commander_id == this.commander.id, flora.scans))
                scan: int = scan_list[0].count if scan_list else 0
                waypoints: list[Waypoint] = list(
                    filter(
                        lambda item: item.commander_id == this.commander.id and item.type == 'tag',
                        flora.waypoints
                    )
                )
                if 0 < scan < 3:
                    if not this.current_scan:
                        this.current_scan = genus
                    distance = get_distance()
                    distance_format = f'{distance:.2f}' if distance is not None else 'unk'
                    distance = distance if distance is not None else 0
                    waypoint = get_nearest(genus, waypoints) if (waypoints and this.waypoints_enabled.get()) else ''
                    text += '\nIn Progress: {} - {} ({}/3) [{}]{}'.format(
                        bio_types[genus][species]['name'],
                        scan_label(scan),
                        scan,
                        '{}/{}m'.format(
                            distance_format
                            if distance < bio_genus[genus]['distance']
                            else f'> {bio_genus[genus]["distance"]}',
                            bio_genus[genus]['distance']
                        ),
                        f'\nNearest Saved Waypoint: {waypoint}' if waypoint else ''
                    )
                    break

        this.total_label['text'] = 'Analysed System Samples:\n{} | FF: {}'.format(
            this.formatter.format_credits(total_value),
            this.formatter.format_credits((total_value * 5)))
    else:
        this.scroll_canvas.grid_remove()
        this.scrollbar.grid_remove()
        this.total_label.grid_remove()
        text = 'BioScan: No Signals Found'
        this.total_label['text'] = ''

    this.label['text'] = text
    this.values_label['text'] = detail_text

    # if this.show_details.get():
    #     this.scroll_canvas.grid()
    #     this.scrollbar.grid()
    # else:
    #     this.scroll_canvas.grid_remove()
    #     this.scrollbar.grid_remove()


def bind_mousewheel(event: tk.Event) -> None:
    """ Scroll pane mousewheel bind on mouseover """

    if sys.platform in ('linux', 'cygwin', 'msys'):
        this.scroll_canvas.bind_all('<Button-4>', on_mousewheel)
        this.scroll_canvas.bind_all('<Button-5>', on_mousewheel)
    else:
        this.scroll_canvas.bind_all('<MouseWheel>', on_mousewheel)


def unbind_mousewheel(event: tk.Event) -> None:
    """ Scroll pane mousewheel unbind on mouseout """

    if sys.platform in ('linux', 'cygwin', 'msys'):
        this.scroll_canvas.unbind_all('<Button-4>')
        this.scroll_canvas.unbind_all('<Button-5>')
    else:
        this.scroll_canvas.unbind_all('<MouseWheel>')


def on_mousewheel(event: tk.Event) -> None:
    """ Scroll pane mousewheel event handler """

    shift = (event.state & 0x1) != 0
    scroll = 0
    if event.num == 4 or event.delta == 120:
        scroll = -1
    if event.num == 5 or event.delta == -120:
        scroll = 1
    if shift:
        this.scroll_canvas.xview_scroll(scroll, 'units')
    else:
        this.scroll_canvas.yview_scroll(scroll, 'units')
