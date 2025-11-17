#!/usr/bin/env python3
# -*- coding: UTF-8 -*-.

import geopandas as gpd
from uuid import uuid4
import json,urllib.request
from numpy import ndarray
from geoalchemy2 import Geometry
from sqlalchemy import text

dict_columns = {
    'code_zh': 'code',
    'nom_zh': 'main_name',
    'date_visite':'create_date',
    'remark_activity': 'global_remark_activity'
}

dict_type_milieux = {
    'FU':'Fourré humide',
    'BFH':'Boisement feuillu humide',
    'BCH':'Boisement de conifères humide',
    'AL':'Végétation herbacée pionnière des alluvions',
    'GH':'Communauté de grands hélophytes',
    'RB':'Communauté de petits hélophytes',
    'MC':'Magnocariçaie',
    'BM':'Bas-marais et marais de transition',
    'MG':'Mégaphorbiaie',
    'AQ':'Végétation aquatique',
    'FO':'Végétation fontinale (sources, mouillères)',
    'EC':"Végétation amphibie des bordure d'eaux courantes",
    'PH':'Prairie humide (et pelouse humide)',
}


def normalize_connexion(con):
    if gpd.pd.__version__>'2.1.2':
        return con.raw_connection()
    else:
        return con


def get_cor_impact_types(con):
    res = gpd.pd.read_sql_table('cor_impact_types',con,'pr_zh')
    return dict(zip(res.id_impact,res.id_cor_impact_types))


def get_nomenclature_id(con,cd,typ):
    """
    Get the id_nomenclature for a given cd and typ.

    Parameters
    ----------
    con : sqlalchemy.engine.Engine
        The database connection.
    cd : str
        The code of the nomenclature.
    typ : str
        The type of the nomenclature.

    Returns
    -------
    int or None
        The id of the nomenclature or None if the nomenclature does not exist.
    """
    sql = """
        SELECT ref_nomenclatures.get_id_nomenclature('{typ}', '{cd}') 
    """.format(cd=cd,typ=typ)
    with con.begin() as cnx:
        res = cnx.execute(text(sql))

    return res.one()[0] if res else None


def _cor_zh_area(tzh_code,con,typ,cover=False):
    """
    @tzh : pd.Serie. Série de valeurs 
        correspondants à la colonne pr_zh.t_zh."code".
    @typ : str.  COM, DEP, ref_geo
    """
    from math import ceil

    table = 'cor_zh_area'

    sqltzh = """
        SELECT zh.id_zh, zh.geom FROM pr_zh.t_zh zh WHERE zh."code" in {tzh_code}
    """.format(tzh_code=tuple(tzh_code))
    tzh = gpd.read_postgis(sqltzh,con,crs=4326)

    if tzh.crs.srs.lower()=='epsg:4326':
        tzh.to_crs(2154,inplace=True)

    sqllarea = """
        SELECT l.id_area, l.geom FROM ref_geo.l_areas l
        JOIN ref_geo.bib_areas_types bib USING (id_type)
        WHERE bib.type_code='{typ}' and l."enable"
    """.format(typ=typ)
    larea = gpd.read_postgis(sqllarea,con,crs=2154)

    df = _calc_recouvrmt(larea,tzh).rename(columns={'perc_rcvmt':'cover'})

    if cover:
        df['cover'] = [ceil(x) for x in df.cover]  
    else :
        df.drop(columns=['cover'],inplace=True)

    # return df
    if not df.empty:
        df.to_sql(
            name=table, con=con, schema='pr_zh', 
            if_exists='append', index=False
        )
        print('INSERT %i correspondances'%df.shape[0])
    else:
        print('AUCUNE nouvelles correspondances identifiées')


def _calc_recouvrmt(df1,df2,how='inner'):
    '''
    Calcule le recouvrement de df2 sur df1
    pour chaque géométrie de df1:
    Parameters
    ----------
    df1 : GeoDataFrame.
    df2 : GeoDataFrame.
    '''
    iddf1 = df1.columns[0]
    iddf2 = df2.columns[0]

    # Jointure spaciale
    tmp = gpd.sjoin(
        df1,
        df2[['geom']],
        predicate = 'intersects', 
        how = how)

    tmp.dropna(subset=['index_right'],inplace=True)
    tmp.index_right = tmp.index_right.astype(int)
    tmp.reset_index(inplace=True)

    tmp = tmp.join(
        df2[['geom',iddf2]].rename(columns={'geom': 'right_geom'}), 
        on=['index_right'], how='left')

    tmp2 = tmp[['index_right','right_geom',iddf2]].copy() \
        .rename(columns={'right_geom': 'geom'}) \
        .set_geometry('geom')

    tmp1 = tmp[[iddf1,'geom']].copy() \
        .set_geometry('geom')

    if not tmp1.geom.values.is_valid.all():
        tmp1.loc[~tmp1.geom.values.is_valid,'geom'] = tmp1.loc[~tmp1.geom.values.is_valid,'geom'].buffer(0)

    if not tmp2.geom.values.is_valid.all():
        tmp2.loc[~tmp2.geom.values.is_valid,'geom'] = tmp2.loc[~tmp2.geom.values.is_valid,'geom'].buffer(0)

    tmp['perc_rcvmt'] = (tmp1.intersection(tmp2).area/tmp1.area)*100

    return tmp[[iddf1,iddf2,'perc_rcvmt']]


def _cor_zh_(tzh_code,con,typ):
    """
    @tzh : pd.Serie. Série de valeurs 
        correspondants à la colonne pr_zh.t_zh."code".
    @typ : str. [hydro,rb]
    """
    typ = typ.lower()
    table = 'cor_zh_%s'%typ
    tab_typ = 't_hydro_area' if typ == 'hydro' else 't_river_basin'
    id_typ  = 'id_hydro' if typ == 'hydro' else 'id_rb'
    sql = '''
        SELECT h.{id_typ},zh.id_zh
        FROM pr_zh.{tab_typ} h, pr_zh.t_zh zh
        WHERE zh."code" in {tzh_code}
        AND ST_INTERSECTS( ST_SetSRID(h.geom,4326),ST_MakeValid(ST_SetSRID(zh.geom,4326)))
        AND (h.{id_typ},zh.id_zh) NOT IN (SELECT {id_typ},id_zh FROM pr_zh.{tab_to})
    ;'''.format(
        tzh_code = tuple(tzh_code),
        id_typ   = id_typ,
        tab_typ  = tab_typ,
        tab_to   = table)
    df = gpd.pd.read_sql_query(sql,con)

    if not df.empty:
        df.to_sql(
            name=table, con=con, schema='pr_zh', 
            if_exists='append', index=False
        )
        print('INSERT %i correspondances'%df.shape[0])
    else:
        print('AUCUNE nouvelles correspondances identifiées')



def to_tzh(df,con,lst_area):
    tab = 't_zh'
    sch = 'pr_zh'
    con.connect()
    lst_columns = [
        x['name'] for x in con.dialect.get_columns(con.connect(),tab,sch) if x['name'] in df.columns
    ]

    # con = normalize_connexion(con)
    to_ins = df[lst_columns].copy()

    lst = []
    for r,row in to_ins.iterrows():
        tmplst = []
        for v, vals in row.items():
            if isinstance(vals,(str,int)):
                if isinstance(vals,str) and vals.__contains__("'"):
                    vals = vals.replace("'","''")
                tmplst.append(vals)
            else:
                tmplst.append(str(vals))
        lst.append(str(tuple(tmplst)))

    sqlValue = ','.join(lst).replace('"',"'").replace("'nan'",'NULL')
    sql = '''
        INSERT INTO pr_zh.t_zh ({cols}) VALUES {vals}
        RETURNING id_zh,code;
    '''.format(
        cols=','.join(to_ins.columns),
        vals=sqlValue
    )


    try:
        with con.begin() as cnx:
            _res = cnx.execute(text(sql))
            res = _res.fetchall()
        # to_ins.to_postgis(tab,con,sch,if_exists='append',index=False,)
    except Exception as e:
        raise e    
    
    res_df = gpd.pd.DataFrame(res)
    for area in lst_area:
        cov = True if area == 'COM' else False
        _cor_zh_area(tzh_code=res_df.code,con=con,typ=area,cover=cov)
        print('INSERT cor_zh_area %s OK !'%area)
    
    _cor_zh_(tzh_code=res_df.code,con=con,typ='hydro')
    print('INSERT cor_zh_hydro OK !')
    _cor_zh_(tzh_code=res_df.code,con=con,typ='rb')
    print('INSERT cor_zh_rb OK !')

    print("INSERT data TO t_zh OK !")
    return res_df
        # return gpd.pd.read_sql(
        #     'SELECT id_zh,code FROM pr_zh.t_zh WHERE code {sym} {lst};'.format(
        #         sym='IN' if to_ins.shape[0] > 1 else '=',
        #         lst=str(tuple(to_ins.code)) if to_ins.shape[0] > 1 else "'%s'"%to_ins.code.to_list()[0]
        #     ),
        #     con=con)
        # id_zh = df[['code','pk']].rename(columns={'pk':'id_zh'})

def to_cor_lim_list(cor_lim,con):
    """
    Insert into cor_lim_list the list of delimitation for each zh area
    :param cor_lim: a dataframe with columns id_lim_list,cd_nomenclature_delimitation
    :param con: a sqlalchemy connection
    :return: None
    """
    typ_class = list(set([tup.cd_nomenclature_delimitation.__class__ for tup in cor_lim.itertuples()]))
    if typ_class[0] == list:
        _cor_lim = (cor_lim
            .set_index('id_lim_list')
            .cd_nomenclature_delimitation
            .explode()
            .to_frame('cd_lim'))
    else:
        _cor_lim = (cor_lim
            .set_index('id_lim_list')
            .cd_nomenclature_delimitation.str.split(',',expand=True)
            .stack()
            .str.strip()
            .to_frame('cd_lim')
            .droplevel(-1)
        )
    _cor_lim['id_lim'] = [get_nomenclature_id(con,x,'CRIT_DELIM') for x in _cor_lim.cd_lim]
    _cor_lim.drop('cd_lim',axis=1,inplace=True)

    con = normalize_connexion(con)
    try:
        _cor_lim.to_sql(
            name='cor_lim_list',
            con=con,
            schema='pr_zh',
            index=True,
            if_exists='append',
            method='multi'
        )
    except Exception as e:
        print(e)
    finally:
        print("INSERT Délimitation TO cor_lim_list OK !")

def to_cor_zh_cb(id_zh,df,con):
    """
    Insert habitats to cor_zh_cb table.

    Parameters
    ----------
    id_zh : pd.DataFrame
        DataFrame containing 'code' and 'id_zh' columns.
    df : pd.DataFrame
        DataFrame containing 'code' and 'habitat_corine_biotope' columns.
    con : sqlalchemy.engine.Engine
        Connection to the geonature database.

    Returns
    -------
    None
    """

    cor_zh_cb = (df
        .merge(id_zh,how='left',on='code')
        .set_index('id_zh')
        .habitat_corine_biotope.str.split(',')
        .explode()
        .str.strip()
        .to_frame('lb_code')
    )

    con = normalize_connexion(con)
    try:
        cor_zh_cb.to_sql(
            name='cor_zh_cb',
            con=con,
            schema='pr_zh',
            index=True,
            if_exists='append',
            method='multi'
        )
    except Exception as e:
        print(e)
    finally:
        print("INSERT habitats to cor_zh_cb OK !")


def check_cd_nomenclature_impact(cd):
    dizaine = range(0,100,10)
    _cd = [x+'.0' if float(x) not in dizaine and not x.endswith('.0') else x for x in cd]
    return _cd


def to_t_activity(id_zh,actv,con):
    _activ = (actv
        .merge(id_zh,how='left',on='code')
        .drop('code',axis=1))
    
    t_activ = gpd.pd.DataFrame()
    for i,x in _activ.iterrows():
        if isinstance(x['acti_impact'],str):
            res = gpd.pd.DataFrame(json.loads(x['acti_impact']))
        else : 
            res = gpd.pd.DataFrame(x['acti_impact'])
        res['id_zh'] = x['id_zh']
        t_activ = gpd.pd.concat([t_activ,res])
    t_activ.set_index('id_zh',inplace=True)
    t_activ.dropna(inplace=True,axis=1,how='all')
    t_activ.dropna(inplace=True,axis=0,how='all')

    t_activ['id_activity'] = [get_nomenclature_id(con,x,'ACTIV_HUM') for x in t_activ.cd_activite_humaine]
    t_activ['id_position'] = [get_nomenclature_id(con,x,'LOCALISATION') for x in t_activ.localisation]
    t_activ['id_impact_list'] = [uuid4() for _ in range(len(t_activ))]

    impact_list = (t_activ[['id_impact_list','impact']]
        .set_index('id_impact_list')
        .impact.explode()
        .to_frame('cd_impact')
        .reset_index()
    )
    impact_list['_cd_impact'] = check_cd_nomenclature_impact(impact_list.cd_impact)
    impact_list['id_cor_impact_types'] = [get_nomenclature_id(con,x,'IMPACTS') for x in impact_list._cd_impact]

    cor_imp_typ = get_cor_impact_types(con)
    impact_list.replace({'id_cor_impact_types':cor_imp_typ}, inplace=True)

    t_activ.drop(['cd_activite_humaine','localisation','impact'],axis=1,inplace=True)
    impact_list.drop(['cd_impact','_cd_impact'],axis=1,inplace=True)
    
    con = normalize_connexion(con)
    try:
        t_activ.to_sql(
            name='t_activity',
            con=con,
            schema='pr_zh',
            index=True,
            if_exists='append',
            method='multi'
        )
    except Exception as e:
        print(e)
    finally:
        print("INSERT activity to t_activity OK !")

        try:
            impact_list.to_sql(
                name='cor_impact_list',
                con=con,
                schema='pr_zh',
                index=False,
                if_exists='append',
                method='multi'
            )
        except Exception as e:
            print(e)
        finally:
            print("INSERT impact to cor_impact_list OK !")


def check_habitat(habitat,con):
    sql = '''
        SELECT lb_code FROM pr_zh.bib_cb
        WHERE lb_code {symbol} {code} 
    '''.format(
        symbol='IN' if isinstance(habitat,(list,gpd.pd.Series,ndarray)) else '=',
        code=tuple(habitat) if isinstance(habitat,(list,gpd.pd.Series,ndarray)) else f"'{habitat}'"
    )
    with con.begin() as cnx:
        res = cnx.execute(text(sql)).all()
    return [x[0] for x in res if x]


def filter_habitat(habitat,con):
    iscd_zh = check_habitat(habitat,con)
    _cd_zh = habitat[habitat.isin(iscd_zh)]
    _cd_notzh = habitat[~habitat.isin(iscd_zh)]
    cd_zh = _cd_zh.groupby(_cd_zh.index).aggregate(', '.join)
    cd_notzh = _cd_notzh.groupby(_cd_notzh.index).aggregate(', '.join)
    return cd_zh,cd_notzh


def check_observ(obs,id_org,con):
    _obs = normalize_observers(obs)
    con = normalize_connexion(con)
    sql = '''
        SELECT
            r.id_role AS ids_observers,
            CONCAT(r.nom_role, ' ', prenom_role) AS observers
        FROM utilisateurs.t_roles r
        JOIN utilisateurs.bib_organismes USING (id_organisme)
        WHERE CONCAT(UPPER(r.nom_role), ' ', INITCAP(prenom_role)) {symbol} {code} 
        AND id_organisme = '{org}'
    '''.format(
        symbol='IN' if isinstance(_obs,(list,gpd.pd.Series,ndarray)) else '=',
        code=tuple(_obs) if isinstance(_obs,(list,gpd.pd.Series,ndarray)) else f"'{_obs}'",
        org=id_org
    )
    res = (gpd.pd.read_sql(
            sql,
            con)
        .merge(_obs.to_frame('observers'),how='right',on='observers'))
    return res

def insert_observ(obs,id_org,con):
    check = check_observ(obs,id_org,con)
    usr_missing = check[check.ids_observers.isnull()].drop_duplicates()
    if usr_missing.empty:
        print("Observateurs trouvés dans le schéma `utilisateurs`")
        return check
    Q = input("Observateurs `{}` non trouvés dans le schéma `utilisateurs`, voulez-vous les ajouter ? (y/n) ".format(usr_missing.observers.to_list()))
    if Q.lower() == 'y':
        id_obs_miss = _insert_observ(usr_missing,id_org,con)
        for i,row in id_obs_miss.iterrows():
            check.loc[check.observers==row.observers,'ids_observers'] = row.id_role
        # check = check_observ(obs,id_org,con)
        return check.astype({'ids_observers':int})
    else:
        return 'No'

def _insert_observ(obs,id_org,con):
    _obs = gpd.pd.concat(
        [obs,obs.observers.str.split(' ',n=-1,expand=True)],
        axis=1,ignore_index=True)
    _obs['id_org'] = id_org

    sql ='''
        INSERT INTO utilisateurs.t_roles (nom_role,prenom_role,id_organisme)
            VALUES {vals}
        --ON CONFLICT (nom_organisme) DO NOTHING
        RETURNING id_role, CONCAT(UPPER(nom_role), ' ', INITCAP(prenom_role)) AS observers;
    '''.format(vals=','.join(["('%s','%s',%i)"%(row[3],row[4],row[5]) for row in _obs.itertuples()]))

    # con = normalize_connexion(con)
    # res = gpd.pd.read_sql(sql,con)
    # return res
    with con.begin() as cnx:
        _res = cnx.execute(text(sql))
        res = _res.fetchall()
    return gpd.pd.DataFrame(res)

def normalize_observers(obs):
    _obs = obs.str.split(' ',expand=True)
    _obs[0] = _obs[0].str.upper()
    _obs[1] = _obs[1].str.title()
    _obs_stack = _obs.stack().droplevel(-1)
    return _obs_stack.groupby(_obs_stack.index).aggregate(' '.join)


def select_orga_user(org,con):
    sql ='''
        SELECT id_organisme FROM utilisateurs.bib_organismes
        WHERE nom_organisme = '{nom}'
    '''.format(nom=org[0])
    with con.begin() as cnx:
        _res = cnx.execute(text(sql))
        res = _res.one_or_none()
    return res


def insert_orga_user(org,con):
    res = select_orga_user(org,con)
    if not res :
        Q = input("Organisme `{}` non trouvé dans le schéma `utilisateurs`, voulez-vous l'ajouter ? (y/n) ".format(org[0]))
        if Q.lower() != 'y':
            return 'No'
        sql ='''
            INSERT INTO utilisateurs.bib_organismes (nom_organisme)
                VALUES ('{nom}')
            --ON CONFLICT (nom_organisme) DO NOTHING
            RETURNING id_organisme;
        '''.format(nom=org[0])
        with con.begin() as cnx:
            _res = cnx.execute(text(sql))
            res = _res.one_or_none()
    else:
        print("Organisme `{}` existant dans le schéma `utilisateurs`".format(org[0]))
    return res[0]

def select_orga_przh(org,con):
    sql ='''
        SELECT id_org FROM pr_zh.bib_organismes
        WHERE name = '{nom}';
    '''.format(nom=org[0])
    with con.begin() as cnx:
        _res = cnx.execute(text(sql))
        res = _res.one_or_none()
    return res

def insert_orga_przh(org,con):
    res = select_orga_przh(org,con)
    if not res :
        Q = input("Organisme `{}` non trouvé dans le schéma `pr_zh`, voulez-vous l'ajouter ? (y/n) ".format(org[0]))
        if Q.lower() != 'y':
            return 'No'
        sql ='''
            INSERT INTO pr_zh.bib_organismes (name,abbrevation,is_op_org)
                VALUES ('{nom}', {abbrev}, TRUE)
            --ON CONFLICT (name,abbrevation) DO NOTHING
            RETURNING id_org;
        '''.format(nom=org[0],abbrev=f"'{org[1]}'" if org[1] else 'NULL')
        with con.begin() as cnx:
            _res = cnx.execute(text(sql))
            res = _res.fetchall()
    else:
        print("Organisme `{}` existant dans le schéma `pr_zh`".format(org[0]))
    return res[0]

def insert_users_missing(user,org,con):
    id_org_przh  = insert_orga_przh(org,con)
    id_user_orga = insert_orga_user(org,con)
    obsv = insert_observ(user,id_user_orga,con)
    return obsv, id_org_przh

def filter_zh(code,con):
    sql ='''
        SELECT code FROM pr_zh.t_zh
        WHERE code IN {}
    '''.format(code)

    con = normalize_connexion(con)
    res = gpd.pd.read_sql(sql,con)
    return res

def insert_zh_fromapi(
    url,con,dep_filter,orga,lst_area=['DEP','COM'],
    prefix_hab_rq='',code_rename={}):
    """
    Insert data from API into geonature database

    Parameters
    ----------
    url : str
        url of the API
    con : sqlalchemy.engine.Engine
        connection to the geonature database
    dep_filter : str
        filter code_zh by this department code
    prefix_hab_rq : str
        prefix to add to remark_pres field for habitats not in geonature database
    code_rename : dict
        Dictionnary of zh code


    Returns
    -------
    None
    """

    api = gpd.read_file(url, engine='fiona')
    if api.empty:
        print("Aucune zone humide trouvée")
        print("Arrêt du script !")
        return None
    
    api.rename(columns=dict_columns, inplace=True)
    if code_rename :
        api.replace({'code':code_rename},inplace=True)

    zh_filter = filter_zh(
        tuple(api.code.dropna()),
        con
    )

    nzh_tobdd = api[
        (api.action=='Créer')&
        (~api.code.isin(zh_filter.code.tolist()))
    ].shape[0]
    print("L'api contient %s zones .."%api.shape[0])
    print("%s zones sont taguée `Problème` .."%api[api.action=='Problème'].shape[0])
    print("%s zones taguée `Créer` existent déjà .."%zh_filter.shape[0])
    print("%s zones taguée `Créer` sont à intégrer .."%nzh_tobdd)

    if nzh_tobdd == 0 :
        print("\nAucune nouvelles zones humides sont à intégrée ..")
        print("Arrêt du script !")
        return None

    Q = input('Souhaitez-vous continuer ? (Y/n) : ')
    if Q.lower() != 'y':
        print('Arrêt du code ..')
        return None

    df = (api[
            (api.action=='Créer')&
            (~api.code.isin(zh_filter.code.tolist()))
        ].rename_geometry('geom')
        # .merge(load_missing_propertie(url,'cd_nomenclature_delimitation',dep_filter), on='pk')
    )

    df.observateur = normalize_observers(df.observateur)
    users, id_org  = insert_users_missing(df.observateur,orga,con)
    df['id_org'] = id_org
    df = (df
        .merge(users.drop_duplicates(),how='left',left_on='observateur',right_on='observers')
        .rename(columns={'ids_observers':'id_role'})
    )

    df['zh_uuid'] = [uuid4() for _ in range(len(df))]
    df['id_lim_list'] = [uuid4() for _ in range(len(df))]
    df['id_sdage'] = [get_nomenclature_id(con,x,'SDAGE') for x in df.cd_typo_sdage]
    df['create_author'] = df.id_role.copy()
    df['update_author'] = df.id_role.copy()

    cd_hab = (df
        .set_index('code')
        .habitat_corine_biotope
        .str.split(',').explode()
        .str.strip())

    hab_zh,hab_notzh = filter_habitat(cd_hab,con)
    _df = (df
        .drop('habitat_corine_biotope',axis=1)
        .merge(hab_zh,how='left',right_index=True,left_on='code')
        .sort_values('code'))
    
    # Si pas de champs remark_pres dans le tableau
    if 'remark_pres' not in _df.columns:
        _df = _df.merge(
            prefix_hab_rq + hab_notzh.rename('remark_pres'),
            how='left',right_index=True,left_on='code'
        )
    else : 
        _df.remark_pres = (_df.remark_pres
            .str.split('_',expand=True)[0]
            .replace(dict_type_milieux)
        )
        _df.set_index('code',inplace=True)
        _df.loc[_df.index.isin(hab_notzh.index),'remark_pres'] = (
            _df[_df.index.isin(hab_notzh.index.tolist())]
            .remark_pres
            + '\n' + prefix_hab_rq + hab_notzh
        )
        _df.reset_index(drop=False,inplace=True)

    c = _df[_df.action=="Créer"].copy()
    u = _df[_df.action=="Modifier"].copy()

    if not c.empty:
        return c
        # id_zh = to_tzh(c,con,lst_area)
        # to_cor_zh_cb(id_zh,c,con)
        # to_cor_lim_list(c[['id_lim_list','cd_nomenclature_delimitation']],con)
        # to_t_activity(id_zh,c[['code','acti_impact']],con)
    if not u.empty:
        raise('Script à coder !')


def load_missing_propertie(url,propertie,dep_filter='38'):
    """
    Load from api the missing properties for the given url, propertie and dep_filter.

    Parameters
    ----------
    url : str
        url of the api
    propertie : str
        name of the propertie to retrieve
    dep_filter : str
        filter for the department, default is '38'
    
    Returns
    -------
    pd.DataFrame
        DataFrame with columns 'code' and propertie
    """

    data = urllib.request.urlopen(url).read()
    output = json.loads(data)
    features = output['items']['features']

    res = {
        'pk':[x['properties']['pk'] for x in features if x['properties']['departement'] == dep_filter],
        propertie:[
            ','.join(x['properties'][propertie]) 
            for x in features 
            if x['properties']['departement'] == dep_filter],
    }
    return gpd.pd.DataFrame(res)

if __name__ == "__main__":

    from sqlalchemy import create_engine
    from sqlalchemy.engine import URL
    # Parametres bdd 
    user = 'xxxxxxx'
    pwd  = "xxxxxxx"
    adr  = 'xxxxxxx'
    base = 'xxxxxxx'
    url  = URL.create("postgresql+psycopg2", username=user, password=pwd, host=adr, database=base)
    con_gn  = create_engine(url)

    # Numéro de département permettant d'identifier les zones humides concernées par le territoire
    # ['38', '05'], default : 38
    dep_filter = 'xx'
    # Préfixe ajouté dans le champs remark_pres lorsque des habitats décrits ne sont pas des habitats dit "humides" dans la listes des habitats humides définis en bdd (table bib_cb)
    prefix_hab_rmk = 'Autre(s) habitat(s) décrit(s) :\n'
    # [Nom de l'organisme, Abbreviation]
    organisme = ['Parc national des Écrins','PNE']

    # Liste des type_area à intersecter pour remplir la table cor_zh_area
    listAreaCover = ['DEP','COM','APB','ZPS','SIC','ZNIEFF1','ZNIEFF2']
    
    # Code zh de l'api à renomer si souhaité. Défaut = {}
    code_rename = {}

    api = 'https://geonature.ecrins-parcnational.fr/api/exports/api/21?departement=%s'%dep_filter
    res = insert_zh_fromapi(
        url=api,
        con=con_gn,
        dep_filter=dep_filter,
        orga=organisme,
        lst_area=listAreaCover,
        prefix_hab_rq=prefix_hab_rmk,
        code_rename=code_rename
    )
    
