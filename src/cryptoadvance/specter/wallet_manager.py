import os, json, logging, shutil
from collections import OrderedDict
from .descriptor import AddChecksum
from .helpers import alias, load_jsons
from .rpc import get_default_datadir, RpcError
from .specter_error import SpecterError
from .wallets.wallet import Wallet


logger = logging.getLogger(__name__)

purposes = OrderedDict({
    None: "General",
    "wpkh": "Single (Segwit)",
    "sh-wpkh": "Single (Nested)",
    "pkh": "Single (Legacy)",
    "wsh": "Multisig (Segwit)",
    "sh-wsh": "Multisig (Nested)",
    "sh": "Multisig (Legacy)",
})

addrtypes = {
    "pkh": "legacy",
    "sh-wpkh": "p2sh-segwit",
    "wpkh": "bech32",
    "sh": "legacy",
    "sh-wsh": "p2sh-segwit",
    "wsh": "bech32"
}

class WalletManager:
    # chain is required to manage wallets when bitcoin-cli is not running
    def __init__(self, data_folder, cli, chain, device_manager, path="specter"):
        self.data_folder = data_folder
        self.chain = chain
        self.cli = cli
        self.cli_path = path
        self.device_manager = device_manager
        self.is_loading = False
        self.update(data_folder, cli, chain)

    def update(self, data_folder=None, cli=None, chain=None):
        if self.is_loading:
            return
        self.is_loading = True
        if chain is not None:
            self.chain = chain
        if data_folder is not None:
            self.data_folder = data_folder
            if data_folder.startswith("~"):
                data_folder = os.path.expanduser(data_folder)
            # creating folders if they don't exist
            if not os.path.isdir(data_folder):
                os.mkdir(data_folder)
        self.working_folder = None
        if self.chain is not None and self.data_folder is not None:
            self.working_folder = os.path.join(self.data_folder, self.chain)
        if self.working_folder is not None and not os.path.isdir(self.working_folder):
            os.mkdir(self.working_folder)
        if cli is not None:
            self.cli = cli

        self.wallets = {}
        if self.working_folder is not None:
            wallets_files = load_jsons(self.working_folder, key="name")
            existing_wallets = [w["name"] for w in self.cli.listwalletdir()["wallets"]]
            loaded_wallets = self.cli.listwallets()
            not_loaded_wallets = [w for w in existing_wallets if w not in loaded_wallets]
            for wallet in wallets_files:
                wallet_alias = wallets_files[wallet]["alias"]
                wallet_name = wallets_files[wallet]["name"]
                if os.path.join(self.cli_path, wallet_alias) in existing_wallets:
                    if os.path.join(self.cli_path, wallet_alias) in not_loaded_wallets:
                        try:
                            logger.debug("loading %s " % wallets_files[wallet]["alias"])
                            self.cli.loadwallet(os.path.join(self.cli_path, wallet_alias))
                            self.wallets[wallet_name] = Wallet.from_json(wallets_files[wallet], self.device_manager, self)
                            # Lock UTXO of pending PSBTs
                            if len(self.wallets[wallet_name].pending_psbts) > 0:
                                for psbt in self.wallets[wallet_name].pending_psbts:
                                    logger.debug("lock %s " % wallet_alias, self.wallets[wallet_name].pending_psbts[psbt]["tx"]["vin"])
                                    self.wallets[wallet_name].cli.lockunspent(False, [utxo for utxo in self.wallets[wallet_name].pending_psbts[psbt]["tx"]["vin"]])
                        except RpcError:
                            logger.warn("Couldn't load wallet %s into core. Silently ignored!" % wallet_alias)
                    elif os.path.join(self.cli_path, wallet_alias) in loaded_wallets:
                        self.wallets[wallet_name] = Wallet.from_json(wallets_files[wallet], self.device_manager, self)
                else:
                    logger.warn("Couldn't find wallet %s in core's wallets. Silently ignored!" % wallet_alias)
        self.is_loading = False

    def get_by_alias(self, alias):
        for wallet_name in self.wallets:
            if self.wallets[wallet_name].alias == alias:
                return self.wallets[wallet_name]
        raise SpecterError("Wallet %s does not exist!" % alias)

    @property
    def wallets_names(self):
        return sorted(self.wallets.keys())

    def _get_initial_wallet_dict(self, name):
        walletsindir = [wallet["name"] for wallet in self.cli.listwalletdir()["wallets"]]
        al = alias(name)
        i = 2
        while os.path.isfile(os.path.join(self.working_folder, "%s.json" % al)) or os.path.join(self.cli_path,al) in walletsindir:
            al = alias("%s %d" % (name, i))
            i += 1
        dic = {
            "alias": al,
            "fullpath": os.path.join(self.working_folder, "%s.json" % al),
            "name": name,
            "address_index": -1,
            "keypool": 0,
            "address": '',
            "change_index": -1,
            "change_address": '',
            "change_keypool": 0,
            "pending_psbts": {}
        }
        return dic

    # TODO: Refactor wallet creation. Maybe...
    def _create_wallet(self, wallet_dict):
        self.cli.createwallet(os.path.join(self.cli_path, wallet_dict["alias"]), True)
        # add wallet to internal dict
        self.wallets[wallet_dict["name"]] = Wallet.from_json(wallet_dict, self.device_manager, self)
        # save wallet file to disk
        if self.working_folder is not None:
            self.wallets[wallet_dict["name"]].save_to_file()
        # get Wallet class instance
        return self.wallets[wallet_dict["name"]]

    def create_simple(self, name, key_type, key, device):
        wallet = self._get_initial_wallet_dict(name)
        arr = key_type.split("-")
        desc = key.metadata['combined']
        recv_desc = "%s/0/*" % desc
        change_desc = "%s/1/*" % desc
        for el in arr[::-1]:
            recv_desc = "%s(%s)" % (el, recv_desc)
            change_desc = "%s(%s)" % (el, change_desc)
        recv_desc = AddChecksum(recv_desc)
        change_desc = AddChecksum(change_desc)
        wallet.update({
            "type": "simple", 
            "description": purposes[key_type],
            "sigs_required": 1,
            "keys": [key.json],
            "recv_descriptor": recv_desc,
            "change_descriptor": change_desc,
            "devices": [device],
            "address_type": addrtypes[key_type],
        })

        return self._create_wallet(wallet)

    # TODO: Refactor to sorted descriptor
    def create_multi(self, name, sigs_required, key_type, keys, devices):
        wallet = self._get_initial_wallet_dict(name)
        # TODO: refactor, ugly
        arr = key_type.split("-")
        descs = [key.metadata['combined'] for key in keys]
        recv_descs = ["%s/0/*" % desc for desc in descs]
        change_descs = ["%s/1/*" % desc for desc in descs]
        recv_desc = "sortedmulti({},{})".format(sigs_required, ",".join(recv_descs))
        change_desc = "sortedmulti({},{})".format(sigs_required, ",".join(change_descs))
        for el in arr[::-1]:
            recv_desc = "%s(%s)" % (el, recv_desc)
            change_desc = "%s(%s)" % (el, change_desc)
        recv_desc = AddChecksum(recv_desc)
        change_desc = AddChecksum(change_desc)
        wallet.update({
            "type": "multisig",
            "description": "{} of {} {}".format(sigs_required, len(keys), purposes[key_type]),
            "sigs_required": sigs_required,
            "keys": [key.json for key in keys],
            "recv_descriptor": recv_desc,
            "change_descriptor": change_desc,
            "devices": devices,
            "address_type": addrtypes[key_type]
        })

        return self._create_wallet(wallet)

    def delete_wallet(self, wallet):
        logger.info("Deleting {}".format(wallet.alias))
        self.cli.unloadwallet(os.path.join(self.cli_path, wallet.alias))
        # Try deleting wallet file
        if get_default_datadir() and os.path.exists(os.path.join(get_default_datadir(), os.path.join(self.cli_path, wallet.alias))):
            shutil.rmtree(os.path.join(get_default_datadir(), os.path.join(self.cli_path, wallet.alias)))
        # Delete JSON
        if os.path.exists(wallet.fullpath):
            os.remove(wallet.fullpath)
        self.update()

    def rename_wallet(self, wallet, name):
        logger.info("Renaming {}".format(wallet.alias))
        wallet.name = name
        if self.working_folder is not None:
            wallet.save_to_file()
        self.update()
