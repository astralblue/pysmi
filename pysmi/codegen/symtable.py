#
# This file is part of pysmi software.
#
# Copyright (c) 2015-2016, Ilya Etingof <ilya@glas.net>
# License: http://pysmi.sf.net/license.html
#
# Build an internally used symbol table for each passed MIB.
#
import sys
from keyword import iskeyword
from pysmi.mibinfo import MibInfo
from pysmi.codegen.base import AbstractCodeGen
from pysmi import error
from pysmi import debug

if sys.version_info[0] > 2:
    # noinspection PyShadowingBuiltins
    unicode = str
    # noinspection PyShadowingBuiltins
    long = int


    def dorepr(s):
        return repr(s)
else:
    def dorepr(s):
        return repr(s.encode('utf-8')).decode('utf-8')


class SymtableCodeGen(AbstractCodeGen):
    symsTable = {
        'MODULE-IDENTITY': ('ModuleIdentity',),
        'OBJECT-TYPE': ('MibScalar', 'MibTable', 'MibTableRow', 'MibTableColumn'),
        'NOTIFICATION-TYPE': ('NotificationType',),
        'TEXTUAL-CONVENTION': ('TextualConvention',),
        'MODULE-COMPLIANCE': ('ModuleCompliance',),
        'OBJECT-GROUP': ('ObjectGroup',),
        'NOTIFICATION-GROUP': ('NotificationGroup',),
        'AGENT-CAPABILITIES': ('AgentCapabilities',),
        'OBJECT-IDENTITY': ('ObjectIdentity',),
        'TRAP-TYPE': ('NotificationType',),  # smidump always uses NotificationType
        'BITS': ('Bits',),
    }

    constImports = {
        'SNMPv2-SMI': ('iso',
                       'Bits',  # XXX
                       'Integer32',  # XXX
                       'TimeTicks',  # bug in some IETF MIBs
                       'Counter32',  # bug in some IETF MIBs (e.g. DSA-MIB)
                       'Counter64',  # bug in some MIBs (e.g.A3COM-HUAWEI-LswINF-MIB)
                       'NOTIFICATION-TYPE',  # bug in some MIBs (e.g. A3COM-HUAWEI-DHCPSNOOP-MIB)
                       'Gauge32',  # bug in some IETF MIBs (e.g. DSA-MIB)
                       'MODULE-IDENTITY', 'OBJECT-TYPE', 'OBJECT-IDENTITY', 'Unsigned32', 'IpAddress',  # XXX
                       'MibIdentifier'),  # OBJECT IDENTIFIER
        'SNMPv2-TC': ('DisplayString', 'TEXTUAL-CONVENTION',),  # XXX
        'SNMPv2-CONF': ('MODULE-COMPLIANCE', 'NOTIFICATION-GROUP',),  # XXX
    }

    baseTypes = ['Integer', 'Integer32', 'Bits', 'ObjectIdentifier', 'OctetString']

    typeClasses = {
        'COUNTER32': 'Counter32',
        'COUNTER64': 'Counter64',
        'GAUGE32': 'Gauge32',
        'INTEGER': 'Integer32',  # XXX
        'INTEGER32': 'Integer32',
        'IPADDRESS': 'IpAddress',
        'NETWORKADDRESS': 'IpAddress',
        'OBJECT IDENTIFIER': 'ObjectIdentifier',
        'OCTET STRING': 'OctetString',
        'OPAQUE': 'Opaque',
        'TIMETICKS': 'TimeTicks',
        'UNSIGNED32': 'Unsigned32',
        'Counter': 'Counter32',
        'Gauge': 'Gauge32',
        'NetworkAddress': 'IpAddress',  # RFC1065-SMI, RFC1155-SMI -> SNMPv2-SMI
        'nullSpecific': 'zeroDotZero',  # RFC1158-MIB -> SNMPv2-SMI
        'ipRoutingTable': 'ipRouteTable',  # RFC1158-MIB -> RFC1213-MIB
        'snmpEnableAuthTraps': 'snmpEnableAuthenTraps'  # RFC1158-MIB -> SNMPv2-MIB
    }

    smiv1IdxTypes = ['INTEGER', 'OCTET STRING', 'IPADDRESS', 'NETWORKADDRESS']
    ifTextStr = 'if mibBuilder.loadTexts: '
    indent = ' ' * 4
    fakeidx = 1000  # starting index for fake symbols

    def __init__(self):
        self._rows = set()
        self._cols = {}  # k, v = name, datatype
        self._exports = set()
        self._postponedSyms = {}  # k, v = symbol, (parents, properties)
        self._parentOids = set()
        self._importMap = {}  # k, v = symbol, MIB
        self._symsOrder = []
        self._out = {}  # k, v = symbol, properties
        self.moduleName = ['DUMMY']
        self.genRules = {'text': 1}

    def symTrans(self, symbol):
        if symbol in self.symsTable:
            return self.symsTable[symbol]
        return symbol,

    @staticmethod
    def transOpers(symbol):
        if iskeyword(symbol):
            symbol = 'pysmi_' + symbol
        return symbol.replace('-', '_')

    @staticmethod
    def isBinary(s):
        return isinstance(s, (str, unicode)) and s[0] == '\'' \
               and s[-2:] in ('\'b', '\'B')

    @staticmethod
    def isHex(s):
        return isinstance(s, (str, unicode)) and s[0] == '\'' \
               and s[-2:] in ('\'h', '\'H')

    def str2int(self, s):
        if self.isBinary(s):
            if s[1:-2]:
                i = int(s[1:-2], 2)
            else:
                raise error.PySmiSemanticError('empty binary string to int conversion')
        elif self.isHex(s):
            if s[1:-2]:
                i = int(s[1:-2], 16)
            else:
                raise error.PySmiSemanticError('empty hex string to int conversion')
        else:
            i = int(s)
        return i

    def prepData(self, pdata, classmode=0):
        data = []
        for el in pdata:
            if not isinstance(el, tuple):
                data.append(el)
            elif len(el) == 1:
                data.append(el[0])
            else:
                data.append(
                    self.handlersTable[el[0]](self, self.prepData(el[1:], classmode=classmode), classmode=classmode)
                    )
        return data

    def genImports(self, imports):
        # convertion to SNMPv2
        toDel = []
        for module in list(imports):
            if module in self.convertImportv2:
                for symbol in imports[module]:
                    if symbol in self.convertImportv2[module]:
                        toDel.append((module, symbol))
                        for newImport in self.convertImportv2[module][symbol]:
                            newModule, newSymbol = newImport
                            if newModule in imports:
                                imports[newModule].append(newSymbol)
                            else:
                                imports[newModule] = [newSymbol]
        # removing converted symbols
        for d in toDel:
            imports[d[0]].remove(d[1])
        # merging mib and constant imports
        for module in self.constImports:
            if module in imports:
                imports[module] += self.constImports[module]
            else:
                imports[module] = self.constImports[module]

        for module in sorted(imports):
            symbols = ()
            for symbol in set(imports[module]):
                symbols += self.symTrans(symbol)
            if symbols:
                self._importMap.update([(self.transOpers(s), module) for s in symbols])
        return {}, tuple(sorted(imports))

    def allParentsExists(self, parents):
        parentsExists = True
        for parent in parents:
            if not (parent in self._out or
                    parent in self._importMap or
                    parent in self.baseTypes or
                    parent in ('MibTable', 'MibTableRow', 'MibTableColumn') or
                    parent in self._rows):
                parentsExists = False
                break
        return parentsExists

    def regSym(self, symbol, symProps, parents=()):
        if symbol in self._out or symbol in self._postponedSyms:  # add to strict mode - or symbol in self._importMap:
            raise error.PySmiSemanticError('Duplicate symbol found: %s' % symbol)
        if self.allParentsExists(parents):
            self._out[symbol] = symProps
            self._symsOrder.append(symbol)
            self.regPostponedSyms()
        else:
            self._postponedSyms[symbol] = (parents, symProps)

    def regPostponedSyms(self):
        regedSyms = []
        for sym, val in self._postponedSyms.items():
            parents, symProps = val
            if self.allParentsExists(parents):
                self._out[sym] = symProps
                self._symsOrder.append(sym)
                regedSyms.append(sym)
        for sym in regedSyms:
            self._postponedSyms.pop(sym)

        # Clause handlers

    # noinspection PyUnusedLocal
    def genAgentCapabilities(self, data, classmode=0):
        origName, description, oid = data
        pysmiName = self.transOpers(origName)
        symProps = {'type': 'AgentCapabilities',
                    'oid': oid,
                    'origName': origName,
                    }
        self.regSym(pysmiName, symProps)

    # noinspection PyUnusedLocal
    def genModuleIdentity(self, data, classmode=0):
        origName, lastUpdated, organization, contactInfo, description, revisions, oid = data
        pysmiName = self.transOpers(origName)
        symProps = {'type': 'ModuleIdentity',
                    'oid': oid,
                    'origName': origName,
                    }
        self.regSym(pysmiName, symProps)

    # noinspection PyUnusedLocal
    def genModuleCompliance(self, data, classmode=0):
        origName, description, compliances, oid = data
        pysmiName = self.transOpers(origName)
        symProps = {'type': 'ModuleCompliance',
                    'oid': oid,
                    'origName': origName}
        self.regSym(pysmiName, symProps)

    # noinspection PyUnusedLocal
    def genNotificationGroup(self, data, classmode=0):
        origName, objects, description, oid = data
        pysmiName = self.transOpers(origName)
        symProps = {'type': 'NotificationGroup',
                    'oid': oid,
                    'origName': origName,
                    }
        self.regSym(pysmiName, symProps)

    # noinspection PyUnusedLocal
    def genNotificationType(self, data, classmode=0):
        origName, objects, description, oid = data
        pysmiName = self.transOpers(origName)
        symProps = {'type': 'NotificationType',
                    'oid': oid,
                    'origName': origName,
                    }
        self.regSym(pysmiName, symProps)

    # noinspection PyUnusedLocal
    def genObjectGroup(self, data, classmode=0):
        origName, objects, description, oid = data
        pysmiName = self.transOpers(origName)
        symProps = {'type': 'ObjectGroup',
                    'oid': oid,
                    'origName': origName,
                    }
        self.regSym(pysmiName, symProps)

    # noinspection PyUnusedLocal
    def genObjectIdentity(self, data, classmode=0):
        origName, description, oid = data
        pysmiName = self.transOpers(origName)
        symProps = {'type': 'ObjectIdentity',
                    'oid': oid,
                    'origName': origName,
                    }
        self.regSym(pysmiName, symProps)

    # noinspection PyUnusedLocal
    def genObjectType(self, data, classmode=0):
        origName, syntax, units, maxaccess, description, augmention, index, defval, oid = data
        pysmiName = self.transOpers(origName)
        symProps = {'type': 'ObjectType',
                    'oid': oid,
                    'syntax': syntax,  # (type, module), subtype
                    'origName': origName,
                    }
        parents = [syntax[0][0]]
        if augmention:
            parents.append(self.transOpers(augmention))
        if defval:  # XXX
            symProps['defval'] = defval
        if index and index[1]:
            namepart, fakeIndexes, fakeSymSyntax = index
            for fakeIdx, fakeSyntax in zip(fakeIndexes, fakeSymSyntax):
                fakeName = namepart + str(fakeIdx)
                fakeSymProps = {'type': 'fakeColumn',
                                'oid': oid + (fakeIdx,),
                                'syntax': fakeSyntax,
                                'origName': fakeName}
                self.regSym(fakeName, fakeSymProps)
        self.regSym(pysmiName, symProps, parents)

    # noinspection PyUnusedLocal
    def genTrapType(self, data, classmode=0):
        origName, enterprise, variables, description, value = data
        pysmiName = self.transOpers(origName)
        symProps = {'type': 'NotificationType',
                    'oid': enterprise + (0, value),
                    'origName': origName}
        self.regSym(pysmiName, symProps)

    # noinspection PyUnusedLocal
    def genTypeDeclaration(self, data, classmode=0):
        origName, declaration = data
        pysmiName = self.transOpers(origName)
        if declaration:
            parentType, attrs = declaration
            if parentType:  # skipping SEQUENCE case
                symProps = {'type': 'TypeDeclaration',
                            'syntax': declaration,  # (type, module), subtype
                            'origName': origName}
                self.regSym(pysmiName, symProps, [declaration[0][0]])

    # noinspection PyUnusedLocal
    def genValueDeclaration(self, data, classmode=0):
        origName, oid = data
        pysmiName = self.transOpers(origName)
        symProps = {'type': 'MibIdentifier',
                    'oid': oid,
                    'origName': origName}
        self.regSym(pysmiName, symProps)

    # Subparts generation functions
    # noinspection PyUnusedLocal,PyMethodMayBeStatic
    def genBitNames(self, data, classmode=0):
        names = data[0]
        return names
        # done

    # noinspection PyUnusedLocal,PyMethodMayBeStatic
    def genBits(self, data, classmode=0):
        bits = data[0]
        return ('Bits', ''), bits
        # done

    # noinspection PyUnusedLocal,PyUnusedLocal,PyMethodMayBeStatic
    def genCompliances(self, data, classmode=0):
        return ''

    # noinspection PyUnusedLocal
    def genConceptualTable(self, data, classmode=0):
        row = data[0]
        if row[0] and row[0][0]:
            self._rows.add(self.transOpers(row[0][0]))
        return ('MibTable', ''), ''
        # done

    # noinspection PyUnusedLocal,PyUnusedLocal,PyMethodMayBeStatic
    def genContactInfo(self, data, classmode=0):
        return ''

    # noinspection PyUnusedLocal,PyUnusedLocal,PyMethodMayBeStatic
    def genDisplayHint(self, data, classmode=0):
        return ''

    # noinspection PyUnusedLocal
    def genDefVal(self, data, classmode=0):  # XXX should be fixed, see pysnmp.py
        defval = data[0]
        if isinstance(defval, (int, long)):  # number
            val = str(defval)
        elif self.isHex(defval):  # hex
            val = 'hexValue="' + defval[1:-2] + '"'  # not working for Integer baseTypes
        elif self.isBinary(defval):  # binary
            binval = defval[1:-2]
            hexval = binval and hex(int(binval, 2))[2:] or ''
            val = 'hexValue="' + hexval + '"'
        elif isinstance(defval, list):  # bits list
            val = defval
        elif defval[0] == defval[-1] and defval[0] == '"':  # quoted strimg
            val = dorepr(defval[1:-1])
        else:  # symbol (oid as defval) or name for enumeration member
            if defval in self._out or defval in self._importMap:
                val = defval + '.getName()'
            else:
                val = dorepr(defval)
        return val

    # noinspection PyUnusedLocal,PyUnusedLocal,PyMethodMayBeStatic
    def genDescription(self, data, classmode=0):
        return ''

    def genEnumSpec(self, data, classmode=0):
        return self.genBits(data, classmode=classmode)[1]

    def genIndex(self, data, classmode=0):
        indexes = data[0]
        fakeIdxName = 'pysmiFakeCol'
        fakeIndexes, fakeSymsSyntax = [], []
        for idx in indexes:
            idxName = idx[1]
            if idxName in self.smiv1IdxTypes:  # SMIv1 support
                idxType = idxName
                objType = self.typeClasses.get(idxType, idxType)
                objType = self.transOpers(objType)
                fakeIndexes.append(self.fakeidx)
                fakeSymsSyntax.append((('MibTableColumn', ''), objType))
                self.fakeidx += 1
        return fakeIdxName, fakeIndexes, fakeSymsSyntax

    # noinspection PyUnusedLocal,PyUnusedLocal,PyMethodMayBeStatic
    def genIntegerSubType(self, data, classmode=0):
        return ''

    # noinspection PyUnusedLocal,PyUnusedLocal,PyMethodMayBeStatic
    def genMaxAccess(self, data, classmode=0):
        return ''

    # noinspection PyUnusedLocal,PyUnusedLocal,PyMethodMayBeStatic
    def genOctetStringSubType(self, data, classmode=0):
        return ''

    # noinspection PyUnusedLocal
    def genOid(self, data, classmode=0):
        out = ()
        for el in data[0]:
            if isinstance(el, (str, unicode)):
                parent = self.transOpers(el)
                self._parentOids.add(parent)
                out += ((parent, self._importMap.get(parent, self.moduleName[0])),)
            elif isinstance(el, (int, long)):
                out += (el,)
            elif isinstance(el, tuple):
                out += (el[1],)  # XXX Do we need to create a new object el[0]?
            else:
                raise error.PySmiSemanticError('unknown datatype for OID: %s' % el)
        return out

    # noinspection PyUnusedLocal,PyUnusedLocal,PyMethodMayBeStatic
    def genObjects(self, data, classmode=0):
        return ''

    # noinspection PyUnusedLocal,PyUnusedLocal,PyMethodMayBeStatic
    def genTime(self, data, classmode=0):
        return ''

    # noinspection PyUnusedLocal,PyUnusedLocal,PyMethodMayBeStatic
    def genLastUpdated(self, data, classmode=0):
        return ''

    # noinspection PyUnusedLocal,PyUnusedLocal,PyMethodMayBeStatic
    def genOrganization(self, data, classmode=0):
        return ''

    # noinspection PyUnusedLocal,PyUnusedLocal,PyMethodMayBeStatic
    def genRevisions(self, data, classmode=0):
        return ''

    def genRow(self, data, classmode=0):
        row = data[0]
        row = self.transOpers(row)
        return row in self._rows and (('MibTableRow', ''), '') or self.genSimpleSyntax(data, classmode=classmode)

    # noinspection PyUnusedLocal
    def genSequence(self, data, classmode=0):
        cols = data[0]
        self._cols.update(cols)
        return '', ''

    # noinspection PyUnusedLocal
    def genSimpleSyntax(self, data, classmode=0):
        objType = data[0]
        module = ''
        objType = self.typeClasses.get(objType, objType)
        objType = self.transOpers(objType)
        if objType not in self.baseTypes:
            module = self._importMap.get(objType, self.moduleName[0])
        subtype = len(data) == 2 and data[1] or ''
        return (objType, module), subtype

    # noinspection PyUnusedLocal,PyMethodMayBeStatic
    def genTypeDeclarationRHS(self, data, classmode=0):
        if len(data) == 1:
            parentType, attrs = data[0]  # just syntax
        else:
            # Textual convention
            display, syntax = data
            parentType, attrs = syntax
        return parentType, attrs

    # noinspection PyUnusedLocal,PyUnusedLocal,PyMethodMayBeStatic
    def genUnits(self, data, classmode=0):
        return ''

    handlersTable = {
        'agentCapabilitiesClause': genAgentCapabilities,
        'moduleIdentityClause': genModuleIdentity,
        'moduleComplianceClause': genModuleCompliance,
        'notificationGroupClause': genNotificationGroup,
        'notificationTypeClause': genNotificationType,
        'objectGroupClause': genObjectGroup,
        'objectIdentityClause': genObjectIdentity,
        'objectTypeClause': genObjectType,
        'trapTypeClause': genTrapType,
        'typeDeclaration': genTypeDeclaration,
        'valueDeclaration': genValueDeclaration,

        'ApplicationSyntax': genSimpleSyntax,
        'BitNames': genBitNames,
        'BITS': genBits,
        'ComplianceModules': genCompliances,
        'conceptualTable': genConceptualTable,
        'CONTACT-INFO': genContactInfo,
        'DISPLAY-HINT': genDisplayHint,
        'DEFVAL': genDefVal,
        'DESCRIPTION': genDescription,
        'enumSpec': genEnumSpec,
        'INDEX': genIndex,
        'integerSubType': genIntegerSubType,
        'MaxAccessPart': genMaxAccess,
        'Notifications': genObjects,
        'octetStringSubType': genOctetStringSubType,
        'objectIdentifier': genOid,
        'Objects': genObjects,
        'LAST-UPDATED': genLastUpdated,
        'ORGANIZATION': genOrganization,
        'Revisions': genRevisions,
        'row': genRow,
        'SEQUENCE': genSequence,
        'SimpleSyntax': genSimpleSyntax,
        'typeDeclarationRHS': genTypeDeclarationRHS,
        'UNITS': genUnits,
        'VarTypes': genObjects,
    }

    def genCode(self, ast, symbolTable, **kwargs):
        self.genRules['text'] = kwargs.get('genTexts', False)
        self._rows.clear()
        self._cols.clear()
        self._parentOids.clear()
        self._symsOrder = []
        self._postponedSyms.clear()
        self._importMap.clear()
        self._out = {}  # should be new object, do not use `clear` method
        self.moduleName[0], moduleOid, imports, declarations = ast
        out, importedModules = self.genImports(imports or {})
        for declr in declarations or []:
            if declr:
                clausetype = declr[0]
                classmode = clausetype == 'typeDeclaration'
                self.handlersTable[declr[0]](self, self.prepData(declr[1:], classmode), classmode)
        if self._postponedSyms:
            raise error.PySmiSemanticError('Unknown parents for symbols: %s' % ', '.join(self._postponedSyms))
        for sym in self._parentOids:
            if sym not in self._out and sym not in self._importMap:
                raise error.PySmiSemanticError('Unknown parent symbol: %s' % sym)
        self._out['_symtable_order'] = list(self._symsOrder)
        self._out['_symtable_cols'] = list(self._cols)
        self._out['_symtable_rows'] = list(self._rows)
        debug.logger & debug.flagCodegen and debug.logger(
            'canonical MIB name %s (%s), imported MIB(s) %s, Symbol table size %s symbols' % (
                self.moduleName[0], moduleOid, ','.join(importedModules) or '<none>', len(self._out)))
        return MibInfo(oid=None, name=self.moduleName[0], imported=tuple([x for x in importedModules])), self._out
